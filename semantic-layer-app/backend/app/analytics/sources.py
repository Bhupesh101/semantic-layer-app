"""Build analysis inputs from a semantic model, so analyses work on any dataset (P10-04).

Columns are found by role and name; the caller (usually the LLM) can name them explicitly.
"""

import re
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd
from sqlglot import exp

from ..query import engine
from ..query.compiler import CompileError, Resolver, compile_query, metric_sql
from ..query.expressions import parse_expression
from ..query.spec import Filter, SemanticQuery
from ..semantic.schema import Column, SemanticModel
from ..services.ingestion import quote_ident

_CUSTOMER = re.compile(r"customer|client|user|buyer|member|account|email", re.I)
_TRANSACTION = re.compile(r"invoice|order|transaction|receipt|basket|bill", re.I)
_MONEY = re.compile(r"revenue|sales|amount|spend|value|gmv", re.I)


class AnalysisInputError(ValueError):
    pass


def _pick(model: SemanticModel, given: Optional[str], purpose: str, rx: re.Pattern, roles: Tuple[str, ...]) -> Column:
    resolver = Resolver(model)
    if given:
        try:
            return resolver.column(given, purpose)
        except CompileError as e:
            raise AnalysisInputError(str(e)) from e
    cols = [c for c in model.columns if c.role in roles]
    hit = next((c for c in cols if rx.search(c.name) or rx.search(c.display_name or "")), None)
    if hit is None:
        raise AnalysisInputError(
            f"No {purpose} column found; pass it explicitly (one of: {', '.join(c.name for c in cols)})."
        )
    return hit


def time_column(model: SemanticModel, given: Optional[str]) -> Column:
    if given:
        try:
            col = Resolver(model).column(given, "time_dimension")
        except CompileError as e:
            raise AnalysisInputError(str(e)) from e
    else:
        col = next(
            (c for c in model.columns if c.role == "time_dimension" and c.data_type in ("date", "datetime")), None
        )
        if col is None:
            raise AnalysisInputError("This dataset has no date/datetime time dimension.")
    if col.data_type not in ("date", "datetime"):
        raise AnalysisInputError(f"'{col.name}' is not a date/datetime column.")
    return col


def monetary_metric(model: SemanticModel, given: Optional[str]) -> str:
    """A metric that can be summed per customer: its top-level aggregate must be SUM."""
    names = (
        [given] if given else [m.name for m in model.metrics if _MONEY.search(m.name)] + [m.name for m in model.metrics]
    )
    resolver = Resolver(model)
    for name in names:
        try:
            sql, out = metric_sql(model, resolver, name)
        except CompileError as e:
            if given:
                raise AnalysisInputError(str(e)) from e
            continue
        node = parse_expression(sql)
        if isinstance(node, exp.Filter):
            node = node.this
        if isinstance(node, exp.Sum):
            return out.source
        if given:
            raise AnalysisInputError(f"'{name}' is not a sum, so it can't be added up per customer.")
    raise AnalysisInputError("No sum-type metric (such as revenue) found; pass monetary_metric explicitly.")


def load_transactions(
    model: SemanticModel,
    parquet: Path,
    customer: Optional[str],
    transaction: Optional[str],
    time_dim: Optional[str],
    metric: Optional[str],
    timeout_s: float,
) -> Tuple[pd.DataFrame, dict]:
    """One row per (customer, transaction) with positive value. PII customer IDs stay local."""
    cust = _pick(model, customer, "customer", _CUSTOMER, ("identifier", "dimension"))
    txn = _pick(model, transaction, "transaction", _TRANSACTION, ("identifier", "dimension"))
    t = time_column(model, time_dim)
    m = monetary_metric(model, metric)
    m_sql, _ = metric_sql(model, Resolver(model), m)
    c, x, d = quote_ident(cust.name), quote_ident(txn.name), quote_ident(t.name)
    sql = (
        f"SELECT {c} AS customer, {x} AS transaction, max({d}) AS date, {m_sql} AS amount "
        f"FROM {engine.SOURCE_VIEW} WHERE {c} IS NOT NULL AND {d} IS NOT NULL GROUP BY 1, 2 HAVING {m_sql} > 0"
    )
    df = engine.fetch_df(parquet, sql, timeout_s=timeout_s)
    df["date"] = pd.to_datetime(df["date"])
    used = {"customer": cust.name, "transaction": txn.name, "time_dimension": t.name, "monetary_metric": m}
    return df, used


def load_series(
    model: SemanticModel,
    parquet: Path,
    metric: str,
    grain: str,
    time_dim: Optional[str],
    filters: List[Filter],
    timeout_s: float,
) -> Tuple[pd.Series, pd.Timestamp, pd.Timestamp, dict]:
    """The metric per period via the normal semantic compiler, plus the data's first and last timestamp."""
    t = time_column(model, time_dim)
    q = SemanticQuery(metrics=[metric], time_dimension=t.name, time_grain=grain, filters=filters)
    try:
        compiled = compile_query(model, q, row_cap=100_000)
    except CompileError as e:
        raise AnalysisInputError(str(e)) from e
    df = engine.fetch_df(parquet, compiled.sql, compiled.params, timeout_s=timeout_s)
    time_out = next(o for o in compiled.columns if o.kind == "time")
    metric_out = next(o for o in compiled.columns if o.kind == "metric")
    s = pd.Series(df[metric_out.name].astype(float).values, index=pd.to_datetime(df[time_out.name]))
    d = quote_ident(t.name)
    rng = engine.fetch_df(
        parquet, f"SELECT min({d}) AS a, max({d}) AS b FROM {engine.SOURCE_VIEW}", timeout_s=timeout_s
    )
    used = {"metric": metric_out.source, "time_dimension": t.name, "filters": compiled.applied_filters}
    return s.dropna(), pd.Timestamp(rng["a"].iloc[0]), pd.Timestamp(rng["b"].iloc[0]), used
