import os
import random
import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="sla-test-"))
os.environ["SLA_DATA_DIR"] = str(_TMP / "data")
os.environ["SLA_LOG_JSON"] = "false"
os.environ["SLA_LOG_LEVEL"] = "WARNING"

CITIES = ["Mumbai", "Delhi", "Pune", "Chennai", "Kolkata"]
STATUSES = ["completed", "completed", "completed", "cancelled", "refunded"]


def make_orders_csv(path: Path, n: int = 300, delimiter: str = ",", malformed: bool = False) -> Path:
    rnd = random.Random(7)
    start = date(2024, 1, 1)
    lines = [delimiter.join(["Order ID", "Order Date", "Customer Email", "City", "Status", "Amount", "Quantity"])]
    for i in range(1, n + 1):
        d = start + timedelta(days=rnd.randint(0, 540))
        lines.append(
            delimiter.join(
                [
                    f"ORD-{i:05d}",
                    d.isoformat(),
                    f"user{rnd.randint(1, 80)}@example.com",
                    rnd.choice(CITIES),
                    rnd.choice(STATUSES),
                    f"{rnd.randint(100, 50000) / 10:.1f}",
                    str(rnd.randint(1, 9)),
                ]
            )
        )
        if malformed and i == 10:
            lines.append(delimiter.join(["ORD-BROKEN", "2024-02-01"]))
    path.write_text("\n".join(lines) + "\n")
    return path


@pytest.fixture(scope="session")
def tmp_root() -> Path:
    yield _TMP
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture(scope="session")
def orders_csv(tmp_root: Path) -> Path:
    return make_orders_csv(tmp_root / "orders.csv")


@pytest.fixture(scope="session")
def orders_parquet(tmp_root: Path, orders_csv: Path):
    from app.services import ingestion

    opts = ingestion.detect_options(orders_csv)
    out = tmp_root / "orders.parquet"
    column_map, rows, _ = ingestion.convert_to_parquet(orders_csv, out, opts)
    return out, column_map, rows


@pytest.fixture(scope="session")
def orders_profile(orders_parquet):
    from app.services.profiler import profile_parquet

    path, column_map, _ = orders_parquet
    return profile_parquet(path, column_map, 200_000)


def orders_model_dict() -> dict:
    return {
        "entity": {
            "name": "orders",
            "display_name": "Orders",
            "description": "One row per order.",
            "grain": "one row per order_id",
        },
        "columns": [
            {
                "name": "order_id",
                "display_name": "Order ID",
                "description": "Order key",
                "data_type": "string",
                "role": "identifier",
                "synonyms": ["order number"],
            },
            {
                "name": "order_date",
                "display_name": "Order Date",
                "description": "Date placed",
                "data_type": "date",
                "role": "time_dimension",
                "synonyms": ["purchase date"],
            },
            {
                "name": "customer_email",
                "display_name": "Customer Email",
                "description": "Email",
                "data_type": "string",
                "role": "identifier",
                "pii": True,
            },
            {
                "name": "city",
                "display_name": "City",
                "description": "Delivery city",
                "data_type": "string",
                "role": "dimension",
                "synonyms": ["town", "location"],
            },
            {
                "name": "status",
                "display_name": "Status",
                "description": "Order status",
                "data_type": "string",
                "role": "dimension",
                "allowed_values": ["completed", "cancelled", "refunded"],
            },
            {
                "name": "amount",
                "display_name": "Amount",
                "description": "Order value in INR",
                "data_type": "float",
                "role": "measure",
                "default_aggregation": "sum",
                "unit": "INR",
                "synonyms": ["sales", "value"],
            },
            {
                "name": "quantity",
                "display_name": "Quantity",
                "description": "Items in the order",
                "data_type": "integer",
                "role": "measure",
                "default_aggregation": "sum",
            },
        ],
        "metrics": [
            {
                "name": "total_revenue",
                "display_name": "Total revenue",
                "description": "Sum of completed amounts",
                "expression": "SUM(amount)",
                "filters": ["status = 'completed'"],
                "synonyms": ["revenue"],
            },
            {
                "name": "order_count",
                "display_name": "Orders",
                "description": "Number of orders",
                "expression": "COUNT(DISTINCT order_id)",
            },
            {
                "name": "avg_order_value",
                "display_name": "AOV",
                "description": "Average order amount",
                "expression": "SUM(amount) / NULLIF(COUNT(DISTINCT order_id), 0)",
            },
            {
                "name": "unique_customers",
                "display_name": "Customers",
                "description": "Distinct customers",
                "expression": "COUNT(DISTINCT customer_email)",
            },
        ],
        "sample_questions": ["Total revenue by month in 2024?", "Top 3 cities by orders"],
    }


@pytest.fixture()
def orders_model():
    from app.semantic.schema import SemanticModel

    return SemanticModel.model_validate(orders_model_dict())
