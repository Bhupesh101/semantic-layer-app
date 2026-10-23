"""Column profiling with DuckDB, no LLM involved (P2-09..P2-13)."""

import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import duckdb

from .ingestion import json_safe, quote_ident, sql_str

TOP_N = 10
SAMPLE_VALUES = 20  # stored; generation sends at most RuntimeSettings.sample_values_per_column

_PATTERNS = {
    "email": re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]+$"),
    "phone": re.compile(r"^\+?[\d\s().-]{7,20}$"),
    "uuid": re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"),
    "currency": re.compile(r"^[-+]?[$€£¥₹]\s?[\d,]+(\.\d+)?$|^[\d,]+(\.\d+)?\s?(USD|EUR|GBP|INR)$"),
    "percentage": re.compile(r"^[-+]?\d+(\.\d+)?\s?%$"),
    "date_string": re.compile(
        r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}([ T]\d{1,2}:\d{2}(:\d{2})?)?$|^\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}$"
    ),
    "boolean_ish": re.compile(r"^(y|n|yes|no|true|false|t|f|0|1)$", re.IGNORECASE),
    "id_code": re.compile(r"^[A-Za-z]{0,4}[-_]?\d{3,}$"),
    "url": re.compile(r"^https?://", re.IGNORECASE),
}
_PII_NAME = re.compile(
    r"(^|_)(e_?mail|phone|mobile|cell|ssn|social_security|passport|aadhaar|pan_?(no|number)?|"
    r"first_?name|last_?name|full_?name|surname|customer_?name|contact_?name|address|street|"
    r"zip|postcode|postal|dob|birth|ip_?address|credit_?card|card_?number|iban|account_?number)(_|$)",
    re.IGNORECASE,
)


def semantic_type(duck_type: str) -> str:
    t = duck_type.upper()
    if t.startswith(
        ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT")
    ):
        return "integer"
    if t.startswith(("DOUBLE", "FLOAT", "REAL", "DECIMAL")):
        return "float"
    if t.startswith("BOOLEAN"):
        return "boolean"
    if t == "DATE":
        return "date"
    if t.startswith("TIMESTAMP"):
        return "datetime"
    return "string"


def _pattern_hints(values: List[str]) -> List[str]:
    if not values:
        return []
    hints = []
    for name, rx in _PATTERNS.items():
        matched = sum(1 for v in values if rx.match(v.strip()))
        if matched / len(values) >= 0.8:
            hints.append(name)
    if "boolean_ish" in hints and len(set(v.lower() for v in values)) > 2:
        hints.remove("boolean_ish")
    return hints


def mask_value(v: Any) -> str:
    s = str(v)
    if "@" in s:
        user, _, domain = s.partition("@")
        return f"{user[:1]}***@{domain}"
    return (s[:1] + "***") if len(s) > 1 else "***"


def profile_parquet(parquet_path: Path, column_map: Dict[str, str], sample_rows: int) -> Dict[str, Any]:
    started = time.perf_counter()
    con = duckdb.connect()
    try:
        p = sql_str(parquet_path)
        row_count = con.execute(f"SELECT count(*) FROM read_parquet({p})").fetchone()[0]
        sampled = row_count > sample_rows
        if sampled:
            con.execute(
                f"CREATE TEMP TABLE t AS SELECT * FROM read_parquet({p}) "
                f"USING SAMPLE reservoir({int(sample_rows)} ROWS) REPEATABLE (42)"
            )
        else:
            con.execute(f"CREATE TEMP VIEW t AS SELECT * FROM read_parquet({p})")
        summary = con.execute("SUMMARIZE t").fetchall()
        sdesc = [d[0] for d in con.description]
        basis = sample_rows if sampled else row_count

        columns: List[Dict[str, Any]] = []
        for row in summary:
            s = dict(zip(sdesc, row))
            name = s["column_name"]
            duck_type = str(s["column_type"])
            q = quote_ident(name)
            stype = semantic_type(duck_type)
            null_pct = float(s["null_percentage"] or 0)
            approx_unique = int(s["approx_unique"] or 0)
            col: Dict[str, Any] = {
                "name": name,
                "original_name": column_map.get(name, name),
                "storage_type": duck_type,
                "inferred_type": stype,
                "null_pct": round(null_pct, 2),
                "distinct_count": approx_unique,
                "distinct_is_estimate": True,
                "min": json_safe(s["min"]),
                "max": json_safe(s["max"]),
            }
            if stype in ("integer", "float"):
                col["mean"] = _num(s["avg"])
                col["median"] = _num(s["q50"])
                col["std"] = _num(s["std"])
            # exact distinct count for low/medium cardinality and PK candidates
            if approx_unique <= 100_000 or approx_unique >= 0.9 * basis:
                col["distinct_count"] = int(con.execute(f"SELECT count(DISTINCT {q}) FROM t").fetchone()[0])
                col["distinct_is_estimate"] = False
            # top values for categoricals / low cardinality
            if stype in ("string", "boolean") or col["distinct_count"] <= 50:
                top = con.execute(
                    f"SELECT {q} AS v, count(*) AS n FROM t WHERE {q} IS NOT NULL "
                    f"GROUP BY 1 ORDER BY n DESC, v LIMIT {TOP_N}"
                ).fetchall()
                col["top_values"] = [{"value": json_safe(v), "count": int(n)} for v, n in top]
            else:
                col["top_values"] = []
            samples = con.execute(f"SELECT DISTINCT {q} FROM t WHERE {q} IS NOT NULL LIMIT {SAMPLE_VALUES}").fetchall()
            col["sample_values"] = [json_safe(r[0]) for r in samples]
            if stype == "string":
                mn, mx, avg = con.execute(
                    f"SELECT min(length({q})), max(length({q})), avg(length({q})) FROM t"
                ).fetchone()
                col["length"] = {"min": mn, "max": mx, "avg": _num(avg)}
                hint_vals = [
                    r[0] for r in con.execute(f"SELECT DISTINCT {q} FROM t WHERE {q} IS NOT NULL LIMIT 200").fetchall()
                ]
                col["pattern_hints"] = _pattern_hints(hint_vals)
            else:
                col["pattern_hints"] = []
            col["pii"] = bool(_PII_NAME.search(name) or {"email", "phone"} & set(col["pattern_hints"]))
            columns.append(col)

        # P2-11: candidate primary keys, verified on the full data
        candidates = []
        for col in columns:
            # floats/dates can be unique by accident; they are never sensible keys
            if col["inferred_type"] not in ("integer", "string"):
                continue
            if col["null_pct"] == 0 and col["distinct_count"] >= 0.99 * basis:
                q = quote_ident(col["name"])
                n_distinct, n_null = con.execute(
                    f"SELECT count(DISTINCT {q}), count(*) - count({q}) FROM read_parquet({p})"
                ).fetchone()
                if n_distinct == row_count and n_null == 0:
                    candidates.append(col["name"])
                    col["candidate_key"] = True
    finally:
        con.close()

    return {
        "row_count": int(row_count),
        "sampled": sampled,
        "sample_rows": int(basis),
        "columns": columns,
        "candidate_keys": candidates,
        "profile_ms": round((time.perf_counter() - started) * 1000),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f, 6)
