"""Programmatic validation of a semantic model against its data (P3-09, P4-12)."""

import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from ..query import engine
from ..query.compiler import available_columns, metric_check
from ..query.expressions import apply_filters, check_expression
from .schema import SemanticModel

_NUMERIC = {"integer", "float"}
_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def issue(path: str, message: str, severity: str = "error") -> Dict[str, str]:
    return {"path": path, "message": message, "severity": severity}


def parse_model(data: Any) -> "tuple[Optional[SemanticModel], List[Dict[str, str]]]":
    try:
        return SemanticModel.model_validate(data), []
    except ValidationError as e:
        return None, [issue(".".join(str(p) for p in err["loc"]), err["msg"]) for err in e.errors()[:50]]


def test_metric(
    model: SemanticModel, parquet_path: Path, expression: str, filters: List[str], timeout_s: float = 10
) -> Dict[str, Any]:
    """Run a metric on the first 10,000 rows (P4-04). Returns {ok, value | error, sql}."""
    errors = metric_check(model, expression, filters)
    if errors:
        return {"ok": False, "error": "; ".join(errors)}
    allowed = available_columns(model)
    node, _ = check_expression(expression, allowed, "aggregate")
    fnodes = [check_expression(f, allowed, "boolean")[0] for f in filters]
    sql_expr = apply_filters(node, fnodes).sql(dialect="duckdb")
    sql = f"SELECT {sql_expr} AS value FROM (SELECT * FROM {engine.SOURCE_VIEW} LIMIT 10000)"
    try:
        res = engine.execute(parquet_path, sql, timeout_s=timeout_s, row_cap=1, use_cache=False)
    except engine.QueryError as e:
        return {"ok": False, "error": str(e), "sql": sql}
    return {"ok": True, "value": res.rows[0][0] if res.rows else None, "sql": sql}


def validate_model(
    model: SemanticModel,
    profile: Dict[str, Any],
    parquet_path: Optional[Path] = None,
    run_metrics: bool = True,
) -> List[Dict[str, str]]:
    issues: List[Dict[str, str]] = []
    prof_cols = {c["name"]: c for c in profile.get("columns", [])}
    names = [c.name for c in model.columns]

    for name, n in Counter(names).items():
        if n > 1:
            issues.append(issue(f"columns.{name}", "Column listed more than once."))
    for name in names:
        if name not in prof_cols:
            issues.append(issue(f"columns.{name}", "Column does not exist in the data."))
    for name in prof_cols:
        if name not in names:
            issues.append(issue(f"columns.{name}", "Column from the data is missing in the model."))

    for c in model.columns:
        path = f"columns.{c.name}"
        prof = prof_cols.get(c.name)
        if c.role == "ignore":
            continue
        if c.role == "measure":
            agg = c.default_aggregation
            if not agg:
                issues.append(issue(path, "Measures need a default aggregation."))
            elif agg in ("sum", "avg") and c.data_type not in _NUMERIC:
                issues.append(issue(path, f"'{agg}' needs a numeric column; this is {c.data_type}."))
            if not c.description.strip():
                issues.append(issue(path, "Measures need a description."))
            if c.pii and agg not in (None, "count", "count_distinct"):
                issues.append(issue(path, "PII measures can only be counted."))
        elif c.default_aggregation and c.role != "identifier":
            issues.append(issue(path, "Only measures use a default aggregation.", "warning"))
        if c.role == "time_dimension" and c.data_type not in ("date", "datetime"):
            issues.append(issue(path, "Time dimensions must be date or datetime columns."))
        if prof is not None:
            stored = prof["inferred_type"]
            if c.data_type in ("date", "datetime") and stored not in ("date", "datetime"):
                issues.append(
                    issue(
                        path,
                        f"Declared {c.data_type} but stored as {stored}; time grains won't work "
                        "until the data is re-parsed as a date.",
                    )
                )
            elif c.data_type in _NUMERIC and stored not in _NUMERIC:
                issues.append(issue(path, f"Declared {c.data_type} but stored as {stored}.", "warning"))
            if prof.get("pii") and not c.pii:
                issues.append(issue(path, "Profiler flagged this column as possible PII.", "warning"))
        if c.pii and c.role in ("dimension", "time_dimension"):
            issues.append(issue(path, "PII columns can't be grouped by or returned; only counted.", "warning"))
        if not c.description.strip():
            issues.append(issue(path, "Missing description.", "warning"))

    metric_names = [m.name for m in model.metrics]
    for name, n in Counter(metric_names).items():
        if n > 1:
            issues.append(issue(f"metrics.{name}", "Duplicate metric name."))
    for m in model.metrics:
        path = f"metrics.{m.name}"
        if not _IDENT.match(m.name):
            issues.append(issue(path, "Metric names must be snake_case identifiers."))
        if m.name in names:
            issues.append(issue(path, "Metric name collides with a column name."))
        if not m.description.strip():
            issues.append(issue(path, "Metrics need a description."))
        errors = metric_check(model, m.expression, m.filters)
        for e in errors:
            issues.append(issue(path, e))
        if not errors and run_metrics and parquet_path is not None:
            res = test_metric(model, parquet_path, m.expression, m.filters)
            if not res["ok"]:
                issues.append(issue(path, f"Expression failed in DuckDB: {res['error']}"))

    if not model.sample_questions:
        issues.append(issue("sample_questions", "Add a few sample questions.", "warning"))
    return issues


def has_errors(issues: List[Dict[str, str]]) -> bool:
    return any(i["severity"] == "error" for i in issues)
