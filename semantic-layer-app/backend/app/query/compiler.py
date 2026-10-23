"""Semantic query spec -> DuckDB SQL (P6-02, P6-03)."""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from sqlglot import exp

from ..semantic.schema import Column, SemanticModel
from ..services.ingestion import quote_ident
from .engine import SOURCE_VIEW
from .expressions import apply_filters, check_expression
from .spec import Filter, SemanticQuery, TimeRange

ROW_COUNT = "row_count"
_AGG_RE = re.compile(r"^\s*(sum|avg|min|max|count|count_distinct)\s*\(\s*([\w ]+?)\s*\)\s*$", re.I)
_AGG_SQL = {
    "sum": "SUM({c})",
    "avg": "AVG({c})",
    "min": "MIN({c})",
    "max": "MAX({c})",
    "count": "COUNT({c})",
    "count_distinct": "COUNT(DISTINCT {c})",
}
_PII_SAFE_AGGS = {"count", "count_distinct"}


class CompileError(ValueError):
    pass


@dataclass
class OutputColumn:
    name: str
    kind: str  # dimension | time | metric
    display_name: str
    source: str  # column or metric it came from
    unit: Optional[str] = None


@dataclass
class CompiledQuery:
    sql: str
    params: List[Any]
    columns: List[OutputColumn]
    applied_filters: List[str] = field(default_factory=list)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.strip().lower()).strip("_")


class Resolver:
    """Maps names, display names and synonyms to columns/metrics (case-insensitive)."""

    def __init__(self, model: SemanticModel):
        self.model = model
        self.lookup: Dict[str, Tuple[str, str]] = {}
        # exact names first, so synonyms never shadow a real name
        for m in model.metrics:
            self.lookup.setdefault(_norm(m.name), ("metric", m.name))
        for c in model.columns:
            if c.role != "ignore":
                self.lookup.setdefault(_norm(c.name), ("column", c.name))
        for m in model.metrics:
            for alias in [m.display_name, *m.synonyms]:
                if alias:
                    self.lookup.setdefault(_norm(alias), ("metric", m.name))
        for c in model.columns:
            if c.role == "ignore":
                continue
            for alias in [c.display_name, *c.synonyms]:
                if alias:
                    self.lookup.setdefault(_norm(alias), ("column", c.name))

    def resolve(self, name: str) -> Tuple[str, str]:
        hit = self.lookup.get(_norm(name))
        if hit is None:
            ignored = self.model.column(name)
            if ignored is not None and ignored.role == "ignore":
                raise CompileError(f"'{name}' is not available for querying.")
            raise CompileError(f"Unknown field '{name}'. Use get_semantic_model to see valid names.")
        return hit

    def column(self, name: str, purpose: str) -> Column:
        kind, resolved = self.resolve(name)
        if kind != "column":
            raise CompileError(f"'{name}' is a metric; a column is required for {purpose}.")
        col = self.model.column(resolved)
        assert col is not None
        return col


def available_columns(model: SemanticModel) -> set:
    return {c.name for c in model.columns if c.role != "ignore"}


def metric_sql(model: SemanticModel, resolver: Resolver, name: str) -> Tuple[str, OutputColumn]:
    """SQL for one requested metric plus its output column description."""
    allowed = available_columns(model)
    if _norm(name) in (ROW_COUNT, "count", "rows") and not model.metric(_norm(name)):
        return "COUNT(*)", OutputColumn(ROW_COUNT, "metric", "Row count", ROW_COUNT)
    m = _AGG_RE.match(name)
    if m:
        agg, col_name = m.group(1).lower(), m.group(2)
        col = resolver.column(col_name, "an aggregation")
        if col.pii and agg not in _PII_SAFE_AGGS:
            raise CompileError(f"'{col.name}' is PII; only count and count_distinct are allowed.")
        alias = f"{agg}_{col.name}"
        return _AGG_SQL[agg].format(c=quote_ident(col.name)), OutputColumn(
            alias,
            "metric",
            f"{agg.replace('_', ' ').title()} of {col.display_name or col.name}",
            col.name,
            col.unit,
        )
    kind, resolved = resolver.resolve(name)
    if kind == "metric":
        metric = model.metric(resolved)
        assert metric is not None
        node, errors = check_expression(metric.expression, allowed, "aggregate")
        filters = []
        for f in metric.filters:
            fnode, ferrs = check_expression(f, allowed, "boolean")
            errors += ferrs
            filters.append(fnode)
        if errors or node is None:
            raise CompileError(f"Metric '{metric.name}' is invalid: {'; '.join(errors)}")
        sql = apply_filters(node, filters).sql(dialect="duckdb")
        return sql, OutputColumn(metric.name, "metric", metric.display_name or metric.name, metric.name, metric.unit)
    col = model.column(resolved)
    assert col is not None
    if col.role != "measure":
        raise CompileError(
            f"'{col.name}' is a {col.role}, not a measure. Use it as a dimension, or " f"count_distinct({col.name})."
        )
    agg = col.default_aggregation or "sum"
    if col.pii and agg not in _PII_SAFE_AGGS:
        raise CompileError(f"'{col.name}' is PII; only count and count_distinct are allowed.")
    return _AGG_SQL[agg].format(c=quote_ident(col.name)), OutputColumn(
        col.name, "metric", col.display_name or col.name, col.name, col.unit
    )


def _default_time_dimension(model: SemanticModel) -> Optional[Column]:
    return next(
        (c for c in model.columns if c.role == "time_dimension" and c.data_type in ("date", "datetime")),
        None,
    )


def filter_sql(model: SemanticModel, resolver: Resolver, f: Filter, params: List[Any]) -> Tuple[str, bool, str]:
    """Return (sql, is_having, human description)."""
    kind, resolved = resolver.resolve(f.field)
    if kind == "metric":
        target, _ = metric_sql(model, resolver, resolved)
        is_having, label = True, resolved
    else:
        col = model.column(resolved)
        assert col is not None
        target, is_having, label = quote_ident(col.name), False, col.name
    op, v = f.op, f.value
    desc = f"{label} {op} {v!r}" if op not in ("is_null", "is_not_null") else f"{label} {op}"
    if op in ("is_null", "is_not_null"):
        return f"{target} IS {'NOT ' if op == 'is_not_null' else ''}NULL", is_having, desc
    if v is None:
        raise CompileError(f"Filter on '{f.field}' with op '{op}' needs a value.")
    if op in ("in", "not_in"):
        values = v if isinstance(v, list) else [v]
        if not values:
            raise CompileError(f"Filter '{op}' on '{f.field}' needs at least one value.")
        params.extend(values)
        marks = ", ".join("?" for _ in values)
        return f"{target} {'NOT ' if op == 'not_in' else ''}IN ({marks})", is_having, desc
    if op == "between":
        if not isinstance(v, list) or len(v) != 2:
            raise CompileError(f"'between' on '{f.field}' needs [low, high].")
        params.extend(v)
        return f"{target} BETWEEN ? AND ?", is_having, desc
    if op in ("contains", "starts_with"):
        params.append(str(v))
        pattern = "'%' || ? || '%'" if op == "contains" else "? || '%'"
        return f"CAST({target} AS VARCHAR) ILIKE {pattern}", is_having, desc
    params.append(v)
    return f"{target} {op} ?", is_having, desc


def _time_range_sql(col: Column, tr: TimeRange, params: List[Any]) -> Tuple[List[str], str]:
    parts, cast = [], "TIMESTAMP" if col.data_type == "datetime" else "DATE"
    if tr.start:
        parts.append(f"{quote_ident(col.name)} >= CAST(? AS {cast})")
        params.append(tr.start)
    if tr.end:
        parts.append(f"{quote_ident(col.name)} < CAST(? AS {cast})")
        params.append(tr.end)
    return parts, f"{col.name} in [{tr.start or '…'}, {tr.end or '…'})"


def compile_query(model: SemanticModel, q: SemanticQuery, row_cap: int) -> CompiledQuery:
    resolver = Resolver(model)
    params_where: List[Any] = []
    params_having: List[Any] = []
    select: List[str] = []
    group: List[str] = []
    outputs: List[OutputColumn] = []
    applied: List[str] = []

    time_col: Optional[Column] = None
    if q.time_dimension:
        time_col = resolver.column(q.time_dimension, "time_dimension")
    elif q.time_grain or q.time_range:
        time_col = _default_time_dimension(model)
        if time_col is None:
            raise CompileError("This dataset has no date/datetime time dimension.")
    if time_col is not None and time_col.data_type not in ("date", "datetime"):
        raise CompileError(f"'{time_col.name}' is not a date/datetime column.")

    if q.time_grain and time_col is not None:
        alias = f"{time_col.name}_{q.time_grain}"
        expr = f"CAST(date_trunc('{q.time_grain}', {quote_ident(time_col.name)}) AS DATE)"
        select.append(f"{expr} AS {quote_ident(alias)}")
        group.append(expr)
        outputs.append(
            OutputColumn(alias, "time", f"{time_col.display_name or time_col.name} ({q.time_grain})", time_col.name)
        )

    seen = {o.name for o in outputs}
    for d in q.dimensions:
        col = resolver.column(d, "a dimension")
        if col.pii:
            raise CompileError(f"'{col.name}' is PII and cannot be used as a dimension.")
        if col.name in seen:
            continue
        seen.add(col.name)
        select.append(quote_ident(col.name))
        group.append(quote_ident(col.name))
        outputs.append(OutputColumn(col.name, "dimension", col.display_name or col.name, col.name, col.unit))

    metrics = q.metrics or [ROW_COUNT]
    for name in metrics:
        sql, out = metric_sql(model, resolver, name)
        if out.name in seen:
            continue
        seen.add(out.name)
        select.append(f"{sql} AS {quote_ident(out.name)}")
        outputs.append(out)

    where: List[str] = []
    having: List[str] = []
    if q.time_range and time_col is not None and (q.time_range.start or q.time_range.end):
        parts, desc = _time_range_sql(time_col, q.time_range, params_where)
        where += parts
        applied.append(desc)
    for f in q.filters:
        target_params: List[Any] = []
        sql, is_having, desc = filter_sql(model, resolver, f, target_params)
        (having if is_having else where).append(sql)
        (params_having if is_having else params_where).extend(target_params)
        applied.append(desc)

    order: List[str] = []
    by_name = {o.name: o for o in outputs}
    for ob in q.order_by:
        key = ob.field
        if key not in by_name:
            # allow ordering by the time column name or a synonym of a selected field
            if time_col is not None and _norm(key) in (_norm(time_col.name), _norm(time_col.display_name or "")):
                key = (
                    next(o.name for o in outputs if o.kind == "time") if any(o.kind == "time" for o in outputs) else key
                )
            else:
                try:
                    _, resolved = resolver.resolve(key)
                    key = resolved
                except CompileError:
                    pass
        if key not in by_name:
            raise CompileError(f"order_by '{ob.field}' must be one of: {', '.join(by_name)}")
        order.append(f"{quote_ident(key)} {ob.direction.upper()}")
    if not order:
        time_out = next((o for o in outputs if o.kind == "time"), None)
        if time_out is not None:
            order.append(f"{quote_ident(time_out.name)} ASC")
        elif group:
            first_metric = next(o for o in outputs if o.kind == "metric")
            order.append(f"{quote_ident(first_metric.name)} DESC")
    # deterministic tie-break on group keys
    order += [f"{g} ASC" for g in group if f"{g} ASC" not in order]

    limit = min(q.limit or row_cap, row_cap)
    sql = f"SELECT {', '.join(select)}, count(*) OVER () AS __total_rows FROM {SOURCE_VIEW}"
    if where:
        sql += " WHERE " + " AND ".join(f"({w})" for w in where)
    if group:
        sql += " GROUP BY " + ", ".join(group)
    if having:
        sql += " HAVING " + " AND ".join(f"({h})" for h in having)
    if order:
        sql += " ORDER BY " + ", ".join(order)
    sql += f" LIMIT {int(limit)}"
    return CompiledQuery(sql, params_where + params_having, outputs, applied)


def compile_get_rows(
    model: SemanticModel,
    columns: List[str],
    filters: List[Filter],
    order_by: List[Any],
    limit: int,
) -> CompiledQuery:
    """P6-10: a small number of raw rows, never PII or ignored columns."""
    resolver = Resolver(model)
    if not columns:
        columns = [c.name for c in model.columns if c.role != "ignore" and not c.pii][:12]
    cols: List[Column] = []
    for name in columns:
        col = resolver.column(name, "get_rows")
        if col.pii:
            raise CompileError(f"'{col.name}' is PII and cannot be returned as raw rows.")
        if col not in cols:
            cols.append(col)
    params: List[Any] = []
    where, applied = [], []
    for f in filters:
        sql, is_having, desc = filter_sql(model, resolver, f, params)
        if is_having:
            raise CompileError("get_rows filters must be on columns, not metrics.")
        where.append(sql)
        applied.append(desc)
    order = []
    for ob in order_by:
        col = resolver.column(ob.field, "order_by")
        order.append(f"{quote_ident(col.name)} {ob.direction.upper()}")
    sql = (
        f"SELECT {', '.join(quote_ident(c.name) for c in cols)}, count(*) OVER () AS __total_rows "
        f"FROM {SOURCE_VIEW}"
    )
    if where:
        sql += " WHERE " + " AND ".join(f"({w})" for w in where)
    if order:
        sql += " ORDER BY " + ", ".join(order)
    sql += f" LIMIT {int(limit)}"
    outputs = [OutputColumn(c.name, "dimension", c.display_name or c.name, c.name, c.unit) for c in cols]
    return CompiledQuery(sql, params, outputs, applied)


def metric_check(model: SemanticModel, expression: str, filters: List[str]) -> List[str]:
    """Static checks for a metric expression (used by validation and the metrics editor)."""
    allowed = available_columns(model)
    node, errors = check_expression(expression, allowed, "aggregate")
    for f in filters:
        _, ferrs = check_expression(f, allowed, "boolean")
        errors += [f"Filter '{f}': {e}" for e in ferrs]
    if node is not None:
        pii = {c.name for c in model.columns if c.pii}
        for agg in node.find_all(exp.AggFunc):
            refs = {c.name for c in agg.find_all(exp.Column)}
            if refs & pii and not isinstance(agg, (exp.Count, exp.ApproxDistinct)):
                errors.append(
                    f"PII column(s) {', '.join(sorted(refs & pii))} may only be counted, not {agg.sql_name()}."
                )
    return errors
