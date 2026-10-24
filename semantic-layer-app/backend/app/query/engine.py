"""Sandboxed, read-only DuckDB execution (P6-04, P6-06, P8-08)."""

import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, List, Optional, Sequence

import duckdb
from pydantic import BaseModel

from ..services.ingestion import json_safe, sql_str

SOURCE_VIEW = "src"
_CACHE_SIZE = 256
_cache: "OrderedDict[tuple, QueryResult]" = OrderedDict()
_cache_lock = threading.Lock()


class QueryError(Exception):
    pass


class QueryTimeout(QueryError):
    pass


class QueryResult(BaseModel):
    columns: List[str]
    rows: List[List[Any]]
    total_rows: int
    truncated: bool
    sql: str
    params: List[Any] = []
    execution_ms: int
    cached: bool = False


def open_sandbox(parquet_path: Path) -> duckdb.DuckDBPyConnection:
    """An in-memory connection that can read exactly one Parquet file and nothing else."""
    path = str(Path(parquet_path).resolve())
    con = duckdb.connect(":memory:")
    con.execute("SET threads=4")
    con.execute(f"CREATE VIEW {SOURCE_VIEW} AS SELECT * FROM read_parquet({sql_str(path)})")
    con.execute(f"SET allowed_paths=[{sql_str(path)}]")
    con.execute("SET enable_external_access=false")
    con.execute("SET autoinstall_known_extensions=false")
    con.execute("SET autoload_known_extensions=false")
    con.execute("SET lock_configuration=true")
    return con


def fetch_df(
    parquet_path: Path,
    sql: str,
    params: Optional[Sequence[Any]] = None,
    *,
    timeout_s: float = 30.0,
    max_rows: int = 5_000_000,
):
    """Run a SELECT in the sandbox and return every row as a DataFrame, for local analyses only (P10-04).

    The result never goes to the LLM as-is; analyses summarise it first (P10-11).
    """
    con = open_sandbox(Path(parquet_path))
    timer = threading.Timer(timeout_s, con.interrupt)
    timer.start()
    try:
        df = con.execute(f"SELECT * FROM ({sql}) AS q LIMIT {int(max_rows) + 1}", list(params or [])).df()
    except duckdb.InterruptException as e:
        raise QueryTimeout(f"Query exceeded the {timeout_s:g}s timeout.") from e
    except duckdb.Error as e:
        if not timer.is_alive():
            raise QueryTimeout(f"Query exceeded the {timeout_s:g}s timeout.") from e
        raise QueryError(str(e).split("\n")[0]) from e
    finally:
        timer.cancel()
        con.close()
    if len(df) > max_rows:
        raise QueryError(f"More than {max_rows:,} rows; narrow the analysis with filters.")
    return df


def execute(
    parquet_path: Path,
    sql: str,
    params: Optional[Sequence[Any]] = None,
    *,
    timeout_s: float = 10.0,
    row_cap: int = 200,
    has_total_column: bool = False,
    use_cache: bool = True,
) -> QueryResult:
    """Run a single SELECT against the `src` view, returning at most `row_cap` rows.

    With `has_total_column`, the SQL (built by the compiler) already ends with a
    `__total_rows` window column and its own LIMIT; otherwise it is wrapped and the
    total is unknown beyond the cap.
    """
    params = list(params or [])
    p = Path(parquet_path)
    key = (str(p), p.stat().st_mtime_ns, sql, repr(params), row_cap)
    if use_cache:
        with _cache_lock:
            hit = _cache.get(key)
            if hit is not None:
                _cache.move_to_end(key)
                return hit.model_copy(update={"cached": True})

    wrapped = (
        f"SELECT * FROM ({sql}) AS q LIMIT {int(row_cap)}"
        if has_total_column
        else f"SELECT * FROM ({sql}) AS q LIMIT {int(row_cap) + 1}"
    )
    con = open_sandbox(p)
    timer = threading.Timer(timeout_s, con.interrupt)
    started = time.perf_counter()
    timer.start()
    try:
        cur = con.execute(wrapped, params)
        cols = [d[0] for d in cur.description]
        raw = cur.fetchall()
    except duckdb.InterruptException as e:
        raise QueryTimeout(f"Query exceeded the {timeout_s:g}s timeout.") from e
    except duckdb.Error as e:
        if not timer.is_alive():
            raise QueryTimeout(f"Query exceeded the {timeout_s:g}s timeout.") from e
        raise QueryError(str(e).split("\n")[0]) from e
    finally:
        timer.cancel()
        con.close()
    elapsed = round((time.perf_counter() - started) * 1000)

    if has_total_column:
        total = int(raw[0][-1]) if raw else 0
        cols = cols[:-1]
        rows = [json_safe(list(r[:-1])) for r in raw]
        truncated = total > len(rows)
    else:
        truncated = len(raw) > row_cap
        rows = [json_safe(list(r)) for r in raw[:row_cap]]
        total = len(rows)
    result = QueryResult(
        columns=cols,
        rows=rows,
        total_rows=total,
        truncated=truncated,
        sql=sql,
        params=json_safe(params),
        execution_ms=elapsed,
    )
    if use_cache:
        with _cache_lock:
            _cache[key] = result
            while len(_cache) > _CACHE_SIZE:
                _cache.popitem(last=False)
    return result
