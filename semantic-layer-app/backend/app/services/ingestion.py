"""CSV upload, parse-option detection, preview and Parquet conversion (P2-01..P2-08)."""

import codecs
import csv
import re
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

import duckdb
from pydantic import BaseModel, Field

PREVIEW_ROWS = 50
_DELIMS = [",", ";", "\t", "|"]


def sql_str(value: Any) -> str:
    """Quote a value as a SQL string literal."""
    return "'" + str(value).replace("'", "''") + "'"


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def json_safe(v: Any) -> Any:
    if isinstance(v, (datetime, date, time)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return round(float(v), 6)
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return None
        # strip binary float noise (691364.5600000108 -> 691364.56) so the LLM repeats clean numbers
        return round(v, 6)
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    if isinstance(v, (list, tuple)):
        return [json_safe(x) for x in v]
    if isinstance(v, dict):
        return {str(k): json_safe(x) for k, x in v.items()}
    return v


class ParseOptions(BaseModel):
    delimiter: Optional[str] = Field(None, max_length=4)
    header: Optional[bool] = None
    encoding: Optional[Literal["utf-8", "latin-1"]] = None
    quote: Optional[str] = Field(None, max_length=1)
    skip_rows: Optional[int] = Field(None, ge=0, le=1000)


def detect_encoding(path: Path) -> str:
    dec = codecs.getincrementaldecoder("utf-8")()
    with open(path, "rb") as f:
        chunk = f.read(4 * 1024 * 1024)
    try:
        dec.decode(chunk, final=False)
        return "utf-8"
    except UnicodeDecodeError:
        return "latin-1"


def _python_sniff_delimiter(path: Path, encoding: str) -> Optional[str]:
    with open(path, "r", encoding=encoding, errors="replace", newline="") as f:
        sample = "".join(f.readline() for _ in range(50))
    try:
        return csv.Sniffer().sniff(sample, delimiters="".join(_DELIMS)).delimiter
    except csv.Error:
        # Fall back to the delimiter that appears most consistently in the header line
        header = sample.splitlines()[0] if sample else ""
        counts = {d: header.count(d) for d in _DELIMS}
        best = max(counts, key=counts.get)
        return best if counts[best] else None


def detect_options(path: Path) -> ParseOptions:
    encoding = detect_encoding(path)
    con = duckdb.connect()
    try:
        delim, quote, has_header, skip = con.execute(
            f"SELECT Delimiter, Quote, HasHeader, SkipRows FROM sniff_csv({sql_str(path)}, "
            f"encoding={sql_str(encoding)}, sample_size=20480)"
        ).fetchone()
    except duckdb.Error:
        delim, quote, has_header, skip = None, None, True, 0
    finally:
        con.close()
    # DuckDB's sniffer can be thrown off by a few malformed rows; double-check when it
    # finds a single column but the header clearly contains another delimiter.
    py_delim = _python_sniff_delimiter(path, encoding)
    if py_delim and (delim is None or (delim != py_delim and _header_has(path, encoding, py_delim))):
        delim = py_delim
    return ParseOptions(
        delimiter=delim or ",",
        header=bool(has_header) if has_header is not None else True,
        encoding=encoding,  # type: ignore[arg-type]
        quote=None if quote in (None, "(empty)", "") else quote,
        skip_rows=int(skip or 0),
    )


def _header_has(path: Path, encoding: str, delim: str) -> bool:
    with open(path, "r", encoding=encoding, errors="replace") as f:
        return delim in f.readline()


def read_csv_expr(path: Path, opts: ParseOptions, full_scan_types: bool = False) -> str:
    args = [sql_str(path)]
    if opts.delimiter:
        args.append(f"delim={sql_str(opts.delimiter)}")
    if opts.header is not None:
        args.append(f"header={'true' if opts.header else 'false'}")
    if opts.encoding:
        args.append(f"encoding={sql_str(opts.encoding)}")
    if opts.quote:
        args.append(f"quote={sql_str(opts.quote)}")
    if opts.skip_rows:
        args.append(f"skip={int(opts.skip_rows)}")
    args.append("ignore_errors=true")
    args.append("store_rejects=true")
    args.append(f"sample_size={-1 if full_scan_types else 20480}")
    return f"read_csv({', '.join(args)})"


def sanitize_columns(names: List[str]) -> List[str]:
    """Make SQL-friendly, unique, lowercase identifiers (P2-06)."""
    out: List[str] = []
    seen: set = set()
    for i, raw in enumerate(names):
        s = re.sub(r"[^0-9a-zA-Z]+", "_", raw.strip()).strip("_").lower() or f"column_{i + 1}"
        if s[0].isdigit():
            s = f"c_{s}"
        base, n = s, 2
        while s in seen:
            s = f"{base}_{n}"
            n += 1
        seen.add(s)
        out.append(s)
    return out


def _rejects(con: duckdb.DuckDBPyConnection) -> Dict[str, Any]:
    try:
        count = con.execute("SELECT count(DISTINCT line) FROM reject_errors").fetchone()[0]
        sample = con.execute(
            "SELECT line, any_value(error_type), any_value(csv_line), any_value(error_message) "
            "FROM reject_errors GROUP BY line ORDER BY line LIMIT 5"
        ).fetchall()
    except duckdb.Error:
        return {"malformed_rows": 0, "malformed_sample": []}
    return {
        "malformed_rows": int(count),
        "malformed_sample": [
            {"line": r[0], "error": r[1], "raw": (r[2] or "")[:300], "message": (r[3] or "")[:300]} for r in sample
        ],
    }


def preview(path: Path, opts: ParseOptions) -> Dict[str, Any]:
    """First rows + detected column types + warnings (P2-04, P2-08)."""
    if path.stat().st_size == 0:
        raise ValueError("The file is empty.")
    con = duckdb.connect()
    try:
        rel = con.execute(f"SELECT * FROM {read_csv_expr(path, opts)} LIMIT {PREVIEW_ROWS}")
        cols = [d[0] for d in rel.description]
        types = [str(d[1]) for d in rel.description]
        rows = [json_safe(list(r)) for r in rel.fetchall()]
        rejects = _rejects(con)
    except duckdb.Error as e:
        raise ValueError(f"Could not parse the file with these options: {e}") from e
    finally:
        con.close()
    if not rows:
        raise ValueError("The file has a header but no data rows.")
    warnings: List[str] = []
    if opts.header is False or all(re.fullmatch(r"column\d+", c) for c in cols):
        warnings.append("No header row detected; columns were named column0, column1, ...")
    dupes = [c for c in cols if re.search(r"_\d+$", c) and c.rsplit("_", 1)[0] in cols]
    if dupes:
        warnings.append(f"Duplicate column headers were renamed: {', '.join(dupes)}")
    if len(cols) == 1:
        warnings.append("Only one column was found. Check the delimiter.")
    if rejects["malformed_rows"]:
        warnings.append(f"{rejects['malformed_rows']} malformed row(s) in the preview window will be skipped.")
    sanitized = sanitize_columns(cols)
    return {
        "options": opts.model_dump(),
        "columns": [{"original": c, "name": s, "type": t} for c, s, t in zip(cols, sanitized, types)],
        "rows": rows,
        "warnings": warnings,
        **rejects,
    }


def convert_to_parquet(
    csv_path: Path, parquet_path: Path, opts: ParseOptions
) -> Tuple[Dict[str, str], int, Dict[str, Any]]:
    """Convert to typed Parquet with sanitized column names (P2-07).

    Returns (column_map sanitized->original, row_count, report).
    """
    con = duckdb.connect()
    try:
        src = read_csv_expr(csv_path, opts, full_scan_types=True)
        cols = [d[0] for d in con.execute(f"SELECT * FROM {src} LIMIT 0").description]
        sanitized = sanitize_columns(cols)
        select = ", ".join(f"{quote_ident(o)} AS {quote_ident(s)}" for o, s in zip(cols, sanitized))
        tmp = parquet_path.with_suffix(".tmp.parquet")
        con.execute(f"COPY (SELECT {select} FROM {src}) TO {sql_str(tmp)} (FORMAT parquet, COMPRESSION zstd)")
        report = _rejects(con)
        rows = con.execute(f"SELECT count(*) FROM read_parquet({sql_str(tmp)})").fetchone()[0]
    except duckdb.Error as e:
        raise ValueError(f"Conversion failed: {e}") from e
    finally:
        con.close()
    if rows == 0:
        tmp.unlink(missing_ok=True)
        raise ValueError("No rows could be read from the file with these parse options.")
    tmp.replace(parquet_path)
    return dict(zip(sanitized, cols)), int(rows), report
