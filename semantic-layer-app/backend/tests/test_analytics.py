import json

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import adjusted_rand_score

from app.analytics.forecast import complete_series, forecast
from app.analytics.segmentation import rfm_scores, rfm_table, segment
from app.chat.tools import ToolContext, run_tool
from app.config import RuntimeSettings


def planted_transactions(seed: int = 3):
    """Three customer groups with clearly different recency, frequency and spend."""
    rnd = np.random.default_rng(seed)
    end = pd.Timestamp("2024-12-31")
    groups = {"vip": (5, 40, 900.0), "regular": (60, 8, 120.0), "lapsed": (400, 2, 40.0)}
    rows, truth = [], {}
    for g, (recency, orders, spend) in groups.items():
        for c in range(60):
            cid = f"{g}-{c}"
            truth[cid] = g
            last = end - pd.Timedelta(days=int(rnd.integers(recency, recency + 10)))
            for o in range(orders):
                rows.append(
                    {
                        "customer": cid,
                        "transaction": f"{cid}-{o}",
                        "date": last - pd.Timedelta(days=int(o * 7)),
                        "amount": float(rnd.normal(spend, spend * 0.1)),
                    }
                )
    return pd.DataFrame(rows), truth


def test_rfm_scores_on_hand_computed_fixture():
    tx = pd.DataFrame(
        {
            "customer": ["a", "a", "b", "c", "c", "c", "d", "e"],
            "transaction": ["1", "2", "3", "4", "5", "6", "7", "8"],
            "date": pd.to_datetime(
                [
                    "2024-01-01",
                    "2024-03-01",
                    "2024-02-01",
                    "2024-03-10",
                    "2024-03-09",
                    "2024-03-08",
                    "2023-06-01",
                    "2024-03-10",
                ]
            ),
            "amount": [10, 20, 5, 100, 100, 100, 1, 50],
        }
    )
    rfm = rfm_table(tx)
    assert rfm.loc["a"].tolist() == [10, 2, 30]  # as-of = 2024-03-11
    assert rfm.loc["c", "frequency"] == 3 and rfm.loc["c", "monetary"] == 300
    scored = rfm_scores(rfm)
    assert scored.loc["c", "r"] >= 4 and scored.loc["d", "r"] == 1  # recent = high
    assert scored["segment"].notna().all()


def test_kmeans_recovers_planted_clusters():
    tx, truth = planted_transactions()
    res = segment(tx, k=3)
    a = res.assignments
    ari = adjusted_rand_score([truth[c] for c in a.index], a["cluster_id"])
    assert ari > 0.9, ari
    assert res.silhouette > 0.5
    assert sum(c["customers"] for c in res.clusters) == 180


def test_segment_picks_k_and_needs_enough_customers():
    tx, _ = planted_transactions()
    res = segment(tx)
    assert res.k == 3 and {s["k"] for s in res.k_scores} == set(range(2, 11))
    with pytest.raises(ValueError, match="at least 20"):
        segment(tx[tx["customer"].isin(["vip-1", "vip-2"])])


def seasonal_series(periods=120, s=12, seed=1):
    rnd = np.random.default_rng(seed)
    t = np.arange(periods)
    y = 1000 + 5 * t + 200 * np.sin(2 * np.pi * t / s) + rnd.normal(0, 20, periods)
    return pd.Series(y, index=pd.date_range("2015-01-01", periods=periods, freq="MS"))


def test_forecast_beats_naive_on_seasonal_series():
    res = forecast(seasonal_series(), "month")
    assert res.seasonal_strength > 0.64 and res.seasonal_order[1] == 1
    assert res.metrics["sarima"]["mae"] < res.metrics["naive"]["mae"]
    assert res.metrics["sarima"]["mape_pct"] < 5
    assert len(res.forecast) == 6
    f = res.forecast[0]
    assert f["lower_95"] <= f["lower_80"] <= f["forecast"] <= f["upper_80"] <= f["upper_95"]
    widths = [r["upper_95"] - r["lower_95"] for r in res.forecast]
    assert widths[-1] >= widths[0]  # intervals widen with the horizon


def test_forecast_edge_cases():
    short = seasonal_series(periods=18)
    res = forecast(short, "month")
    assert res.seasonal_strength is None and any("non-seasonal" in w for w in res.warnings)
    with pytest.raises(ValueError, match="at least 12"):
        forecast(seasonal_series(periods=8), "month")
    flat = pd.Series(100.0, index=pd.date_range("2020-01-06", periods=40, freq="W-MON"))
    assert forecast(flat, "week").forecast[0]["forecast"] == pytest.approx(100, abs=1)


def test_complete_series_fills_gaps_and_drops_partial_periods():
    idx = pd.to_datetime(["2024-01-01", "2024-02-01", "2024-04-01", "2024-05-01"])
    s = pd.Series([1.0, 2.0, 4.0, 5.0], index=idx)
    y, notes = complete_series(s, "month", pd.Timestamp("2024-01-01 09:00"), pd.Timestamp("2024-05-14"))
    assert list(y.index.month) == [1, 2, 3, 4] and y.iloc[2] == 0
    assert any("filled with 0" in n for n in notes) and any("last period" in n for n in notes)


@pytest.fixture()
def ctx(orders_parquet, orders_profile, orders_model):
    return ToolContext(orders_model, orders_profile, orders_parquet[0], RuntimeSettings())


def test_segment_tool_returns_summaries_only(ctx):
    r = run_tool(ctx, "segment_customers", {"method": "rfm"})
    assert r["ok"], r
    used = r["llm"]["columns_used"]
    assert used == {
        "customer": "customer_email",
        "transaction": "order_id",
        "time_dimension": "order_date",
        "monetary_metric": "total_revenue",
    }
    assert "@example.com" not in json.dumps(r["llm"]) + json.dumps(r["ui"])  # PII never leaves (NFR-08)
    assert r["ui"]["columns"][0] == "segment" and r["ui"]["chart"]["kind"] == "bar"
    again = run_tool(ctx, "segment_customers", {"method": "rfm"})
    assert again["ui"]["cached"] is True


def test_segment_tool_rejects_non_sum_metric(ctx):
    r = run_tool(ctx, "segment_customers", {"monetary_metric": "avg_order_value"})
    assert not r["ok"] and "not a sum" in r["llm"]["error"]


def test_forecast_tool(ctx):
    r = run_tool(ctx, "forecast_metric", {"metric": "revenue", "grain": "month", "horizon": 3})
    assert r["ok"], r
    assert r["llm"]["columns_used"]["metric"] == "total_revenue"
    assert len(r["llm"]["forecast"]) == 3 and r["llm"]["confidence"] in ("good", "fair", "poor")
    assert r["ui"]["chart"]["band"] == ["lower_95", "upper_95"]
    future = [row for row in r["ui"]["rows"] if row[1] is None]
    assert len(future) == 3 and all(row[3] <= row[2] <= row[4] for row in future)
    bad = run_tool(ctx, "forecast_metric", {"metric": "city"})
    assert not bad["ok"]
