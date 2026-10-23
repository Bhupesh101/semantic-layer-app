import pytest

from app.chat.tools import ToolContext, ToolError, check_raw_sql, compact_model, run_tool, tool_schemas
from app.config import RuntimeSettings
from app.query import engine
from app.query.expressions import check_expression


@pytest.fixture()
def ctx(orders_parquet, orders_profile, orders_model):
    return ToolContext(orders_model, orders_profile, orders_parquet[0], RuntimeSettings(row_cap=50))


def test_schemas_have_no_refs():
    schemas = tool_schemas(RuntimeSettings())
    names = [s["function"]["name"] for s in schemas]
    assert "run_sql" not in names and "query_metrics" in names
    assert "$ref" not in str(schemas) and "$defs" not in str(schemas)
    assert "get_rows" not in [s["function"]["name"] for s in tool_schemas(RuntimeSettings(aggregates_only=True))]


def test_compact_model_hides_ignored_and_flags_pii(orders_model, orders_profile):
    orders_model.column("quantity").role = "ignore"
    m = compact_model(orders_model, orders_profile)
    names = [c["name"] for c in m["columns"]]
    assert "quantity" not in names
    email = next(c for c in m["columns"] if c["name"] == "customer_email")
    assert "count-only" in email["pii"]
    date_col = next(c for c in m["columns"] if c["name"] == "order_date")
    assert date_col["data_range"][0].startswith("2024")


def test_query_metrics_tool(ctx):
    r = run_tool(ctx, "query_metrics", '{"metrics": ["revenue"], "dimensions": ["city"], "limit": 3}')
    assert r["ok"], r
    assert r["llm"]["columns"] == ["city", "total_revenue"] and len(r["llm"]["rows"]) == 3
    assert "sql" not in r["llm"] and "sql" in r["ui"]


def test_tool_errors_are_structured(ctx):
    r = run_tool(ctx, "query_metrics", {"metrics": ["profit"]})
    assert not r["ok"] and "Unknown field" in r["llm"]["error"]
    r = run_tool(ctx, "query_metrics", "not json")
    assert not r["ok"] and "Invalid arguments" in r["llm"]["error"]
    r = run_tool(ctx, "delete_everything", {})
    assert not r["ok"]


def test_list_dimension_values(ctx):
    r = run_tool(ctx, "list_dimension_values", {"dimension": "town", "search": "MUM"})
    assert r["ok"] and r["llm"]["values"] == ["Mumbai"]
    r = run_tool(ctx, "list_dimension_values", {"dimension": "customer_email"})
    assert not r["ok"] and "PII" in r["llm"]["error"]


def test_describe_column_masks_pii(ctx):
    r = run_tool(ctx, "describe_column", {"column": "customer_email"})
    assert r["ok"]
    assert all("***@" in t["value"] for t in r["llm"]["top_values"])
    assert "min" not in r["llm"]


def test_get_rows_caps_and_aggregates_only(ctx):
    r = run_tool(ctx, "get_rows", {"limit": 50})
    assert r["ok"] and len(r["llm"]["rows"]) == 50 and r["llm"]["truncated"]
    assert "customer_email" not in r["llm"]["columns"]
    ctx.rt.aggregates_only = True
    r = run_tool(ctx, "get_rows", {"limit": 5})
    assert not r["ok"]


def test_row_cap_applies_to_every_tool(ctx):
    ctx.rt.row_cap = 7
    r = run_tool(ctx, "query_metrics", {"metrics": ["amount"], "dimensions": ["order_id"]})
    assert len(r["llm"]["rows"]) == 7 and r["llm"]["total_rows"] == 300


@pytest.mark.parametrize(
    "sql,ok",
    [
        ("SELECT city, SUM(amount) FROM src GROUP BY city", True),
        ("SELECT city, COUNT(DISTINCT customer_email) FROM orders GROUP BY city", True),
        ("WITH t AS (SELECT city, amount FROM src) SELECT city, SUM(amount) AS s FROM t GROUP BY city", True),
        ("SELECT * FROM src", False),
        ("SELECT customer_email FROM src", False),
        ("SELECT MAX(customer_email) FROM src", False),
        ("SELECT city FROM other_table", False),
        ("DELETE FROM src", False),
        ("SELECT 1; SELECT 2", False),
        ("SELECT * FROM read_csv('/etc/passwd')", False),
        ("SELECT getenv('HOME')", False),
    ],
)
def test_raw_sql_guard(orders_model, sql, ok):
    if ok:
        check_raw_sql(orders_model, sql)
    else:
        with pytest.raises(ToolError):
            check_raw_sql(orders_model, sql)


def test_run_sql_disabled_by_default(ctx):
    r = run_tool(ctx, "run_sql", {"query": "SELECT 1"})
    assert not r["ok"]
    ctx.rt.raw_sql_enabled = True
    r = run_tool(ctx, "run_sql", {"query": "SELECT status, COUNT(*) AS n FROM src GROUP BY status ORDER BY n DESC"})
    assert r["ok"] and len(r["llm"]["rows"]) == 3


@pytest.mark.parametrize(
    "expr,kind,ok",
    [
        ("SUM(amount)", "aggregate", True),
        ("SUM(amount) / NULLIF(COUNT(*), 0)", "aggregate", True),
        ("amount", "aggregate", False),
        ("(SELECT SUM(amount) FROM src)", "aggregate", False),
        ("SUM(nope)", "aggregate", False),
        ("status = 'completed'", "boolean", True),
        ("SUM(amount) > 1", "boolean", False),
        ("read_text('/etc/passwd') = ''", "boolean", False),
    ],
)
def test_expression_checks(expr, kind, ok):
    _, errors = check_expression(expr, {"amount", "status"}, kind)
    assert (not errors) == ok, errors


def test_sandbox_blocks_other_files(orders_parquet, tmp_root):
    other = tmp_root / "secret.csv"
    other.write_text("a\n1\n")
    with pytest.raises(engine.QueryError):
        engine.execute(orders_parquet[0], f"SELECT * FROM read_csv('{other}')", use_cache=False)
