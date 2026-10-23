"""Tools exposed to the LLM (P6-07..P6-13). Every tool runs locally against the source."""

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Type

import sqlglot
from pydantic import BaseModel, Field, ValidationError
from sqlglot import exp

from ..config import RuntimeSettings
from ..query import engine
from ..query.compiler import CompileError, Resolver, compile_get_rows, compile_query
from ..query.expressions import _BANNED_FUNCS, _func_name
from ..query.spec import Filter, OrderBy, SemanticQuery
from ..semantic.schema import SemanticModel
from ..services.ingestion import quote_ident
from ..services.profiler import mask_value


@dataclass
class ToolContext:
    model: SemanticModel
    profile: Dict[str, Any]
    parquet_path: Path
    rt: RuntimeSettings


@dataclass
class ToolOutput:
    llm: Dict[str, Any]  # what the model sees (capped)
    ui: Dict[str, Any]  # what the "How I got this" panel shows (adds SQL, timings)


class ToolError(Exception):
    pass


# --------------------------------------------------------------------------- args


class NoArgs(BaseModel):
    pass


class DimensionValuesArgs(BaseModel):
    dimension: str = Field(description="Column name, display name or synonym.")
    search: Optional[str] = Field(None, description="Case-insensitive substring to match.")
    limit: int = Field(25, ge=1, le=100)


class GetRowsArgs(BaseModel):
    columns: List[str] = Field(default_factory=list, description="Columns to return; empty = a default set.")
    filters: List[Filter] = Field(default_factory=list)
    order_by: List[OrderBy] = Field(default_factory=list)
    limit: int = Field(10, ge=1, le=50)


class DescribeColumnArgs(BaseModel):
    column: str


class RunSqlArgs(BaseModel):
    query: str = Field(description="A single read-only SELECT against the table `src`.")


# --------------------------------------------------------------------------- helpers


def compact_model(model: SemanticModel, profile: Dict[str, Any]) -> Dict[str, Any]:
    """The semantic model as the LLM sees it: no ignored columns, no review metadata."""
    prof = {c["name"]: c for c in profile.get("columns", [])}
    cols = []
    for c in model.columns:
        if c.role == "ignore":
            continue
        item: Dict[str, Any] = {"name": c.name, "display_name": c.display_name, "type": c.data_type, "role": c.role}
        if c.description:
            item["description"] = c.description
        if c.role == "measure" and c.default_aggregation:
            item["default_aggregation"] = c.default_aggregation
        for k in ("unit", "format"):
            if getattr(c, k):
                item[k] = getattr(c, k)
        if c.synonyms:
            item["synonyms"] = c.synonyms
        if c.allowed_values and not c.pii:
            item["allowed_values"] = c.allowed_values
        if c.pii:
            item["pii"] = "count-only; never group by or return raw values"
        if c.role == "time_dimension" and c.name in prof:
            item["data_range"] = [prof[c.name].get("min"), prof[c.name].get("max")]
        cols.append(item)
    return {
        "entity": model.entity.model_dump(include={"name", "display_name", "description", "grain"}),
        "row_count": profile.get("row_count"),
        "columns": cols,
        "metrics": [
            {
                k: v
                for k, v in m.model_dump(include={"name", "display_name", "description", "unit", "filters"}).items()
                if v
            }
            for m in model.metrics
        ],
    }


def _result_payload(compiled_cols, res: engine.QueryResult, applied: List[str]) -> ToolOutput:
    llm = {
        "columns": res.columns,
        "rows": res.rows,
        "total_rows": res.total_rows,
        "truncated": res.truncated,
        "filters_applied": applied,
    }
    ui = {
        **llm,
        "sql": res.sql,
        "params": res.params,
        "execution_ms": res.execution_ms,
        "cached": res.cached,
        "column_meta": [
            {"name": c.name, "kind": c.kind, "display_name": c.display_name, "unit": c.unit}
            for c in (compiled_cols or [])
        ],
    }
    return ToolOutput(llm, ui)


def _run(ctx: ToolContext, sql: str, params: List[Any], cap: int, has_total: bool = True) -> engine.QueryResult:
    try:
        return engine.execute(
            ctx.parquet_path,
            sql,
            params,
            timeout_s=ctx.rt.query_timeout_s,
            row_cap=min(cap, ctx.rt.row_cap),
            has_total_column=has_total,
        )
    except engine.QueryError as e:
        raise ToolError(str(e)) from e


# --------------------------------------------------------------------------- tools


def t_get_semantic_model(ctx: ToolContext, _: NoArgs) -> ToolOutput:
    m = compact_model(ctx.model, ctx.profile)
    return ToolOutput(m, m)


def t_list_dimension_values(ctx: ToolContext, a: DimensionValuesArgs) -> ToolOutput:
    try:
        col = Resolver(ctx.model).column(a.dimension, "list_dimension_values")
    except CompileError as e:
        raise ToolError(str(e)) from e
    if col.pii:
        raise ToolError(f"'{col.name}' is PII; its values cannot be listed.")
    q = quote_ident(col.name)
    params: List[Any] = []
    sql = (
        f"SELECT {q} AS value, count(*) AS count, count(*) OVER () AS __total_rows "
        f"FROM {engine.SOURCE_VIEW} WHERE {q} IS NOT NULL"
    )
    if a.search:
        sql += f" AND CAST({q} AS VARCHAR) ILIKE '%' || ? || '%'"
        params.append(a.search)
    sql += f" GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {int(min(a.limit, ctx.rt.row_cap))}"
    res = _run(ctx, sql, params, a.limit)
    out = _result_payload(None, res, [f"search={a.search!r}"] if a.search else [])
    out.llm = {
        "dimension": col.name,
        "values": [r[0] for r in res.rows],
        "counts": [r[1] for r in res.rows],
        "total_distinct": res.total_rows,
        "truncated": res.truncated,
    }
    return out


def t_query_metrics(ctx: ToolContext, q: SemanticQuery) -> ToolOutput:
    try:
        compiled = compile_query(ctx.model, q, ctx.rt.row_cap)
    except CompileError as e:
        raise ToolError(str(e)) from e
    res = _run(ctx, compiled.sql, compiled.params, ctx.rt.row_cap)
    return _result_payload(compiled.columns, res, compiled.applied_filters)


def t_get_rows(ctx: ToolContext, a: GetRowsArgs) -> ToolOutput:
    if ctx.rt.aggregates_only:
        raise ToolError("get_rows is disabled (aggregates-only mode).")
    try:
        compiled = compile_get_rows(ctx.model, a.columns, a.filters, a.order_by, min(a.limit, ctx.rt.get_rows_max))
    except CompileError as e:
        raise ToolError(str(e)) from e
    res = _run(ctx, compiled.sql, compiled.params, ctx.rt.get_rows_max)
    return _result_payload(compiled.columns, res, compiled.applied_filters)


def t_describe_column(ctx: ToolContext, a: DescribeColumnArgs) -> ToolOutput:
    try:
        col = Resolver(ctx.model).column(a.column, "describe_column")
    except CompileError as e:
        raise ToolError(str(e)) from e
    prof = next((c for c in ctx.profile["columns"] if c["name"] == col.name), {})
    keep = ("inferred_type", "null_pct", "distinct_count", "mean", "median", "std", "length", "pattern_hints")
    out: Dict[str, Any] = {
        "name": col.name,
        "display_name": col.display_name,
        "role": col.role,
        "description": col.description,
    }
    out.update({k: prof[k] for k in keep if prof.get(k) is not None})
    if col.pii:
        out["pii"] = True
        out["top_values"] = [
            {"value": mask_value(t["value"]), "count": t["count"]} for t in prof.get("top_values", [])[:5]
        ]
    else:
        out["min"], out["max"] = prof.get("min"), prof.get("max")
        out["top_values"] = prof.get("top_values", [])[:10]
    return ToolOutput(out, out)


def check_raw_sql(model: SemanticModel, query: str) -> str:
    """P6-05: SELECT-only, table allowlist, no PII/ignored columns except inside COUNT."""
    try:
        stmts = sqlglot.parse(query, read="duckdb")
    except sqlglot.errors.ParseError as e:
        raise ToolError(f"Could not parse SQL: {str(e).splitlines()[0]}") from e
    if len(stmts) != 1 or not isinstance(stmts[0], exp.Select):
        raise ToolError("Only a single SELECT statement is allowed.")
    tree = stmts[0]
    ctes = {c.alias for c in tree.find_all(exp.CTE)}
    allowed_tables = {engine.SOURCE_VIEW, model.entity.name} | ctes
    for t in tree.find_all(exp.Table):
        if t.name not in allowed_tables or t.args.get("db") or t.args.get("catalog"):
            raise ToolError(f"Table '{t.sql()}' is not allowed; query `src`.")
        if t.name == model.entity.name and t.name not in ctes:
            t.set("this", exp.to_identifier(engine.SOURCE_VIEW))
    usable = {c.name for c in model.columns if c.role != "ignore"}
    pii = {c.name for c in model.columns if c.pii}
    aliases = {a.alias for a in tree.find_all(exp.Alias)} | {c.alias for c in tree.find_all(exp.TableAlias) if c.alias}
    for node in tree.walk():
        if isinstance(node, exp.Func) and _func_name(node) in _BANNED_FUNCS:
            raise ToolError(f"Function not allowed: {_func_name(node)}.")
        if isinstance(node, exp.Star) and not isinstance(node.parent, exp.Count):
            raise ToolError("SELECT * is not allowed; name the columns.")
        if isinstance(node, exp.Column):
            if node.name not in usable and node.name not in aliases:
                raise ToolError(f"Unknown or unavailable column: {node.name}.")
            if node.name in pii:
                agg = node.find_ancestor(exp.AggFunc)
                if not isinstance(agg, exp.Count):
                    raise ToolError(f"'{node.name}' is PII; it may only appear inside COUNT().")
    return tree.sql(dialect="duckdb")


def t_run_sql(ctx: ToolContext, a: RunSqlArgs) -> ToolOutput:
    if not ctx.rt.raw_sql_enabled:
        raise ToolError("run_sql is disabled.")
    sql = check_raw_sql(ctx.model, a.query)
    res = _run(ctx, sql, [], ctx.rt.row_cap, has_total=False)
    return _result_payload(None, res, [])


# --------------------------------------------------------------------------- registry


@dataclass
class Tool:
    name: str
    description: str
    args: Type[BaseModel]
    fn: Callable[[ToolContext, Any], ToolOutput]
    enabled: Callable[[RuntimeSettings], bool] = lambda rt: True


TOOLS: List[Tool] = [
    Tool(
        "get_semantic_model",
        "Return the semantic model (columns, roles, metrics) of the selected dataset.",
        NoArgs,
        t_get_semantic_model,
    ),
    Tool(
        "list_dimension_values",
        "Distinct stored values of a dimension with counts. "
        "Use it to map user words (e.g. 'UK') to exact values before filtering.",
        DimensionValuesArgs,
        t_list_dimension_values,
    ),
    Tool(
        "query_metrics",
        "Main tool. Aggregate metrics, optionally grouped by dimensions and/or a time grain, "
        "with filters and a time range. Returns a capped table.",
        SemanticQuery,
        t_query_metrics,
    ),
    Tool(
        "get_rows",
        "Fetch a few raw (non-PII) rows for a detail view. Prefer query_metrics for anything numeric.",
        GetRowsArgs,
        t_get_rows,
        lambda rt: not rt.aggregates_only,
    ),
    Tool(
        "describe_column",
        "Profile statistics for one column (types, nulls, distinct count, range, top values).",
        DescribeColumnArgs,
        t_describe_column,
    ),
    Tool(
        "run_sql",
        "Run a read-only DuckDB SELECT against table `src` when query_metrics cannot express the question.",
        RunSqlArgs,
        t_run_sql,
        lambda rt: rt.raw_sql_enabled,
    ),
]


def _inline_refs(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Inline $defs: several OpenAI-compatible servers (e.g. Ollama) don't resolve $ref."""
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(dict(defs[node["$ref"].split("/")[-1]]))
            return {k: walk(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


def tool_schemas(rt: RuntimeSettings) -> List[Dict[str, Any]]:
    out = []
    for t in TOOLS:
        if not t.enabled(rt):
            continue
        params = _inline_refs(t.args.model_json_schema())
        params.setdefault("properties", {})
        params["type"] = "object"
        out.append(
            {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": params}}
        )
    return out


def run_tool(ctx: ToolContext, name: str, raw_args: Any) -> Dict[str, Any]:
    """Execute a tool call. Errors come back as structured results so the LLM can self-correct (P7-11)."""
    started = time.perf_counter()
    tool = next((t for t in TOOLS if t.name == name and t.enabled(ctx.rt)), None)
    if tool is None:
        return _err(name, f"Unknown or disabled tool '{name}'.", started)
    try:
        args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args or "{}")
        if not isinstance(args, dict):
            raise ValueError("Arguments must be a JSON object.")
        parsed = tool.args.model_validate(args)
    except (ValueError, ValidationError) as e:
        return _err(name, f"Invalid arguments: {e}", started)
    try:
        out = tool.fn(ctx, parsed)
    except ToolError as e:
        return _err(name, str(e), started, parsed.model_dump(mode="json"))
    ms = round((time.perf_counter() - started) * 1000)
    return {
        "ok": True,
        "name": name,
        "args": parsed.model_dump(mode="json", exclude_defaults=True),
        "llm": out.llm,
        "ui": {**out.ui, "tool_ms": ms},
    }


def _err(name: str, msg: str, started: float, args: Any = None) -> Dict[str, Any]:
    ms = round((time.perf_counter() - started) * 1000)
    payload = {"error": msg}
    return {"ok": False, "name": name, "args": args, "llm": payload, "ui": {**payload, "tool_ms": ms}}
