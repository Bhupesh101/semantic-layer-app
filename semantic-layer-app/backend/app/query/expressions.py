"""Static checks for SQL expressions written by the LLM or the user (metrics, filters, raw SQL)."""

from typing import Iterable, List, Literal, Optional, Set, Tuple

import sqlglot
from sqlglot import exp

_BANNED_FUNCS = {
    "getenv",
    "query",
    "query_table",
    "glob",
    "read_text",
    "read_blob",
    "read_csv",
    "read_csv_auto",
    "read_parquet",
    "read_json",
    "read_json_auto",
    "read_ndjson",
    "parquet_scan",
    "sniff_csv",
    "pragma",
    "current_setting",
    "duckdb_settings",
}


def _func_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.this).lower()
    return node.sql_name().lower() if isinstance(node, exp.Func) else ""


def parse_expression(text: str) -> exp.Expression:
    try:
        node = sqlglot.parse_one(text, read="duckdb")
    except sqlglot.errors.ParseError as e:
        raise ValueError(f"Could not parse expression: {str(e).splitlines()[0]}") from e
    if node is None:
        raise ValueError("Empty expression.")
    return node


def check_expression(
    text: str,
    allowed_columns: Set[str],
    kind: Literal["aggregate", "boolean", "scalar"],
) -> Tuple[Optional[exp.Expression], List[str]]:
    """Return (parsed node, errors). Rejects subqueries, table refs, windows and file functions."""
    errors: List[str] = []
    try:
        node = parse_expression(text)
    except ValueError as e:
        return None, [str(e)]
    if isinstance(node, (exp.Query, exp.Command, exp.DDL, exp.DML)):
        return None, ["Only expressions are allowed, not statements."]
    for n in node.walk():
        if isinstance(n, (exp.Query, exp.Subquery, exp.Table, exp.Window, exp.Command)):
            errors.append(f"Not allowed in an expression: {n.key}.")
        elif isinstance(n, exp.Func):
            name = _func_name(n)
            if name in _BANNED_FUNCS or type(n).__name__.startswith("Read"):
                errors.append(f"Function not allowed: {name or type(n).__name__}.")
        if isinstance(n, exp.Column):
            if n.table:
                errors.append(f"Qualified column references are not allowed: {n.sql()}.")
            elif n.name not in allowed_columns:
                errors.append(f"Unknown or unavailable column: {n.name}.")
        if isinstance(n, exp.Star) and not isinstance(n.parent, exp.Count):
            errors.append("'*' is only allowed inside COUNT(*).")
    has_agg = any(isinstance(n, exp.AggFunc) for n in node.walk())
    if kind == "aggregate" and not has_agg:
        errors.append("A metric expression must use an aggregate function such as SUM, COUNT or AVG.")
    if kind in ("boolean", "scalar") and has_agg:
        errors.append("Aggregate functions are not allowed here.")
    return node, sorted(set(errors))


def apply_filters(node: exp.Expression, filters: Iterable[exp.Expression]) -> exp.Expression:
    """Attach metric filters to every aggregate: SUM(x) -> SUM(x) FILTER (WHERE cond)."""
    conds = list(filters)
    if not conds:
        return node
    cond = conds[0]
    for c in conds[1:]:
        cond = exp.and_(cond, c)

    def tr(n: exp.Expression) -> exp.Expression:
        if isinstance(n, exp.AggFunc) and not isinstance(n.parent, exp.Filter):
            return exp.Filter(this=n.copy(), expression=exp.Where(this=cond.copy()))
        return n

    return node.transform(tr)


def referenced_columns(node: exp.Expression) -> Set[str]:
    return {c.name for c in node.find_all(exp.Column)}
