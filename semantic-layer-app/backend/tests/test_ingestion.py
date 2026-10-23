from app.services import ingestion
from app.services.profiler import profile_parquet

from .conftest import make_orders_csv


def test_sanitize_columns():
    assert ingestion.sanitize_columns(["Order ID", "order id", "2024 Sales", "", "Name_1"]) == [
        "order_id",
        "order_id_2",
        "c_2024_sales",
        "column_4",
        "name_1",
    ]


def test_detects_semicolon_despite_malformed_row(tmp_root):
    path = make_orders_csv(tmp_root / "semi.csv", n=50, delimiter=";", malformed=True)
    opts = ingestion.detect_options(path)
    assert opts.delimiter == ";"
    assert opts.header is True
    prev = ingestion.preview(path, opts)
    assert [c["name"] for c in prev["columns"]][:2] == ["order_id", "order_date"]
    assert prev["malformed_rows"] == 1
    assert len(prev["rows"]) == 50


def test_latin1_detection(tmp_root):
    path = tmp_root / "latin.csv"
    path.write_bytes("city,v\nS\xe3o Paulo,1\nM\xfcnchen,2\n".encode("latin-1"))
    opts = ingestion.detect_options(path)
    assert opts.encoding == "latin-1"
    assert ingestion.preview(path, opts)["rows"][0][0] == "São Paulo"


def test_empty_file_is_rejected(tmp_root):
    path = tmp_root / "empty.csv"
    path.write_text("")
    try:
        ingestion.preview(path, ingestion.ParseOptions())
    except ValueError as e:
        assert "empty" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_convert_and_profile(orders_parquet, orders_profile):
    _, column_map, rows = orders_parquet
    assert rows == 300
    assert column_map["order_date"] == "Order Date"
    cols = {c["name"]: c for c in orders_profile["columns"]}
    assert cols["order_date"]["inferred_type"] == "date"
    assert cols["amount"]["inferred_type"] == "float"
    assert cols["customer_email"]["pii"] is True
    assert "email" in cols["customer_email"]["pattern_hints"]
    assert cols["city"]["pii"] is False
    assert {t["value"] for t in cols["city"]["top_values"]} == {"Mumbai", "Delhi", "Pune", "Chennai", "Kolkata"}
    assert orders_profile["candidate_keys"] == ["order_id"]


def test_profile_on_sample(orders_parquet):
    path, column_map, _ = orders_parquet
    prof = profile_parquet(path, column_map, sample_rows=1000)
    assert prof["sampled"] is False
    prof = profile_parquet(path, column_map, sample_rows=100)
    assert prof["sampled"] is True and prof["row_count"] == 300
