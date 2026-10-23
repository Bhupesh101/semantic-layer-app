"""P6-14: compiler correctness. Each spec's result must equal a hand-written SQL query."""

import pytest

from app.query import engine
from app.query.compiler import CompileError, compile_get_rows, compile_query
from app.query.spec import Filter, OrderBy, SemanticQuery

REV = "SUM(amount) FILTER (WHERE status = 'completed')"
CASES = [
    # (id, spec, expected SQL over `src`)
    ("row_count", {}, "SELECT COUNT(*) FROM src"),
    ("metric_total_revenue", {"metrics": ["total_revenue"]}, f"SELECT {REV} FROM src"),
    ("metric_synonym", {"metrics": ["revenue"]}, f"SELECT {REV} FROM src"),
    ("metric_display_name", {"metrics": ["AOV"]}, "SELECT SUM(amount) / NULLIF(COUNT(DISTINCT order_id), 0) FROM src"),
    ("measure_default_agg", {"metrics": ["amount"]}, "SELECT SUM(amount) FROM src"),
    ("measure_synonym", {"metrics": ["sales"]}, "SELECT SUM(amount) FROM src"),
    ("adhoc_avg", {"metrics": ["avg(amount)"]}, "SELECT AVG(amount) FROM src"),
    ("adhoc_max", {"metrics": ["max(quantity)"]}, "SELECT MAX(quantity) FROM src"),
    ("adhoc_count_distinct_city", {"metrics": ["count_distinct(city)"]}, "SELECT COUNT(DISTINCT city) FROM src"),
    (
        "pii_count_distinct_ok",
        {"metrics": ["count_distinct(customer_email)"]},
        "SELECT COUNT(DISTINCT customer_email) FROM src",
    ),
    ("metric_unique_customers", {"metrics": ["unique_customers"]}, "SELECT COUNT(DISTINCT customer_email) FROM src"),
    (
        "two_metrics",
        {"metrics": ["order_count", "quantity"]},
        "SELECT COUNT(DISTINCT order_id), SUM(quantity) FROM src",
    ),
    (
        "by_city",
        {"metrics": ["order_count"], "dimensions": ["city"]},
        "SELECT city, COUNT(DISTINCT order_id) c FROM src GROUP BY city ORDER BY c DESC, city",
    ),
    (
        "by_city_synonym",
        {"metrics": ["order_count"], "dimensions": ["town"]},
        "SELECT city, COUNT(DISTINCT order_id) c FROM src GROUP BY city ORDER BY c DESC, city",
    ),
    (
        "by_city_status",
        {"metrics": ["amount"], "dimensions": ["city", "status"]},
        "SELECT city, status, SUM(amount) s FROM src GROUP BY 1, 2 ORDER BY s DESC, city, status",
    ),
    (
        "filter_eq",
        {"metrics": ["order_count"], "filters": [{"field": "status", "value": "cancelled"}]},
        "SELECT COUNT(DISTINCT order_id) FROM src WHERE status = 'cancelled'",
    ),
    (
        "filter_ne",
        {"metrics": ["order_count"], "filters": [{"field": "status", "op": "!=", "value": "cancelled"}]},
        "SELECT COUNT(DISTINCT order_id) FROM src WHERE status <> 'cancelled'",
    ),
    (
        "filter_in",
        {"metrics": ["amount"], "filters": [{"field": "city", "op": "in", "value": ["Pune", "Delhi"]}]},
        "SELECT SUM(amount) FROM src WHERE city IN ('Pune', 'Delhi')",
    ),
    (
        "filter_not_in",
        {"metrics": ["amount"], "filters": [{"field": "city", "op": "not_in", "value": ["Pune"]}]},
        "SELECT SUM(amount) FROM src WHERE city NOT IN ('Pune')",
    ),
    (
        "filter_gt",
        {"metrics": ["row_count"], "filters": [{"field": "amount", "op": ">", "value": 2500}]},
        "SELECT COUNT(*) FROM src WHERE amount > 2500",
    ),
    (
        "filter_between",
        {"metrics": ["row_count"], "filters": [{"field": "quantity", "op": "between", "value": [2, 4]}]},
        "SELECT COUNT(*) FROM src WHERE quantity BETWEEN 2 AND 4",
    ),
    (
        "filter_contains",
        {"metrics": ["row_count"], "filters": [{"field": "city", "op": "contains", "value": "ne"}]},
        "SELECT COUNT(*) FROM src WHERE city ILIKE '%ne%'",
    ),
    (
        "filter_starts_with",
        {"metrics": ["row_count"], "filters": [{"field": "city", "op": "starts_with", "value": "ch"}]},
        "SELECT COUNT(*) FROM src WHERE city ILIKE 'ch%'",
    ),
    (
        "filter_is_not_null",
        {"metrics": ["row_count"], "filters": [{"field": "city", "op": "is_not_null"}]},
        "SELECT COUNT(*) FROM src WHERE city IS NOT NULL",
    ),
    (
        "filter_on_pii_ok",
        {"metrics": ["row_count"], "filters": [{"field": "customer_email", "op": "contains", "value": "user1"}]},
        "SELECT COUNT(*) FROM src WHERE customer_email ILIKE '%user1%'",
    ),
    (
        "filter_on_metric_having",
        {
            "metrics": ["order_count"],
            "dimensions": ["city"],
            "filters": [{"field": "order_count", "op": ">", "value": 55}],
        },
        "SELECT city, COUNT(DISTINCT order_id) c FROM src GROUP BY city HAVING c > 55 ORDER BY c DESC, city",
    ),
    (
        "time_month",
        {"metrics": ["total_revenue"], "time_dimension": "order_date", "time_grain": "month"},
        f"SELECT CAST(date_trunc('month', order_date) AS DATE) m, {REV} FROM src GROUP BY 1 ORDER BY 1",
    ),
    (
        "time_grain_default_dimension",
        {"metrics": ["order_count"], "time_grain": "quarter"},
        "SELECT CAST(date_trunc('quarter', order_date) AS DATE) q, COUNT(DISTINCT order_id) FROM src GROUP BY 1 ORDER BY 1",
    ),
    (
        "time_year_synonym",
        {"metrics": ["order_count"], "time_dimension": "purchase date", "time_grain": "year"},
        "SELECT CAST(date_trunc('year', order_date) AS DATE) y, COUNT(DISTINCT order_id) FROM src GROUP BY 1 ORDER BY 1",
    ),
    (
        "time_week",
        {
            "metrics": ["row_count"],
            "time_dimension": "order_date",
            "time_grain": "week",
            "time_range": {"start": "2024-03-01", "end": "2024-04-01"},
        },
        "SELECT CAST(date_trunc('week', order_date) AS DATE) w, COUNT(*) FROM src "
        "WHERE order_date >= DATE '2024-03-01' AND order_date < DATE '2024-04-01' GROUP BY 1 ORDER BY 1",
    ),
    (
        "time_range_only",
        {"metrics": ["total_revenue"], "time_range": {"start": "2024-01-01", "end": "2025-01-01"}},
        f"SELECT {REV} FROM src WHERE order_date >= DATE '2024-01-01' AND order_date < DATE '2025-01-01'",
    ),
    (
        "time_range_open_end",
        {"metrics": ["row_count"], "time_range": {"start": "2025-01-01"}},
        "SELECT COUNT(*) FROM src WHERE order_date >= DATE '2025-01-01'",
    ),
    (
        "time_and_dimension",
        {"metrics": ["amount"], "dimensions": ["status"], "time_grain": "year"},
        "SELECT CAST(date_trunc('year', order_date) AS DATE) y, status, SUM(amount) FROM src "
        "GROUP BY 1, 2 ORDER BY 1, status",
    ),
    (
        "top3_cities",
        {
            "metrics": ["total_revenue"],
            "dimensions": ["city"],
            "order_by": [{"field": "total_revenue", "direction": "desc"}],
            "limit": 3,
        },
        f"SELECT city, {REV} r FROM src GROUP BY city ORDER BY r DESC, city LIMIT 3",
    ),
    (
        "order_by_dimension_asc",
        {"metrics": ["order_count"], "dimensions": ["city"], "order_by": [{"field": "city", "direction": "asc"}]},
        "SELECT city, COUNT(DISTINCT order_id) FROM src GROUP BY city ORDER BY city",
    ),
    (
        "order_by_synonym",
        {
            "metrics": ["revenue"],
            "dimensions": ["city"],
            "order_by": [{"field": "revenue", "direction": "asc"}],
            "limit": 2,
        },
        f"SELECT city, {REV} r FROM src GROUP BY city ORDER BY r ASC, city LIMIT 2",
    ),
    (
        "combined_filters",
        {
            "metrics": ["avg_order_value"],
            "dimensions": ["city"],
            "filters": [{"field": "status", "value": "completed"}, {"field": "quantity", "op": ">=", "value": 5}],
            "time_range": {"start": "2024-06-01", "end": "2025-01-01"},
        },
        "SELECT city, SUM(amount) / NULLIF(COUNT(DISTINCT order_id), 0) v FROM src "
        "WHERE status = 'completed' AND quantity >= 5 AND order_date >= DATE '2024-06-01' "
        "AND order_date < DATE '2025-01-01' GROUP BY city ORDER BY v DESC, city",
    ),
]


def _run(parquet, sql, params=None, has_total=False):
    return engine.execute(parquet, sql, params or [], row_cap=1000, has_total_column=has_total, use_cache=False)


def _norm(rows):
    return [[round(v, 6) if isinstance(v, float) else v for v in r] for r in rows]


@pytest.mark.parametrize("case_id,spec,expected", CASES, ids=[c[0] for c in CASES])
def test_compiler_matches_handwritten_sql(orders_parquet, orders_model, case_id, spec, expected):
    path = orders_parquet[0]
    compiled = compile_query(orders_model, SemanticQuery.model_validate(spec), row_cap=200)
    got = _run(path, compiled.sql, compiled.params, has_total=True)
    want = _run(path, expected)
    assert _norm(got.rows) == _norm(want.rows), compiled.sql
    assert len(got.rows) > 0


def test_case_count():
    assert len(CASES) >= 30


@pytest.mark.parametrize(
    "spec,message",
    [
        ({"metrics": ["nope"]}, "Unknown field"),
        ({"metrics": ["amount"], "dimensions": ["customer_email"]}, "PII"),
        ({"metrics": ["max(customer_email)"]}, "PII"),
        ({"metrics": ["city"]}, "not a measure"),
        ({"metrics": ["amount"], "dimensions": ["total_revenue"]}, "is a metric"),
        ({"metrics": ["amount"], "time_dimension": "city", "time_grain": "month"}, "not a date"),
        ({"metrics": ["amount"], "filters": [{"field": "city", "op": "in", "value": []}]}, "at least one"),
        ({"metrics": ["amount"], "filters": [{"field": "quantity", "op": "between", "value": [1]}]}, "low, high"),
        ({"metrics": ["amount"], "filters": [{"field": "city", "op": "="}]}, "needs a value"),
        ({"metrics": ["amount"], "dimensions": ["city"], "order_by": [{"field": "status"}]}, "order_by"),
    ],
)
def test_compiler_rejects_bad_input(orders_model, spec, message):
    with pytest.raises(CompileError, match=message):
        compile_query(orders_model, SemanticQuery.model_validate(spec), row_cap=200)


def test_ignored_column_is_rejected(orders_model):
    orders_model.column("city").role = "ignore"
    with pytest.raises(CompileError, match="not available"):
        compile_query(orders_model, SemanticQuery(metrics=["amount"], dimensions=["city"]), row_cap=200)


def test_row_cap_and_truncation(orders_parquet, orders_model):
    compiled = compile_query(orders_model, SemanticQuery(metrics=["amount"], dimensions=["order_id"]), row_cap=25)
    res = engine.execute(orders_parquet[0], compiled.sql, compiled.params, row_cap=25, has_total_column=True)
    assert len(res.rows) == 25 and res.total_rows == 300 and res.truncated


def test_get_rows_excludes_pii(orders_parquet, orders_model):
    with pytest.raises(CompileError, match="PII"):
        compile_get_rows(orders_model, ["order_id", "customer_email"], [], [], 10)
    compiled = compile_get_rows(orders_model, [], [Filter(field="city", value="Pune")], [OrderBy(field="amount")], 5)
    res = engine.execute(orders_parquet[0], compiled.sql, compiled.params, row_cap=5, has_total_column=True)
    assert "customer_email" not in res.columns and len(res.rows) == 5
    assert all(r[res.columns.index("city")] == "Pune" for r in res.rows)


def test_parameters_prevent_injection(orders_parquet, orders_model):
    spec = SemanticQuery(metrics=["row_count"], filters=[Filter(field="city", value="x' OR '1'='1")])
    compiled = compile_query(orders_model, spec, row_cap=10)
    res = engine.execute(orders_parquet[0], compiled.sql, compiled.params, row_cap=10, has_total_column=True)
    assert res.rows == [[0]]
