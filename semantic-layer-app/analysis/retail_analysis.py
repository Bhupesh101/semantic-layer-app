"""Standalone analysis of Online Retail II (2009–2011): RFM + K-means segmentation and SARIMA forecasting.

Uses the same code as the app's chat tools (backend/app/analytics). Run from the project root:

    backend/.venv/bin/python analysis/retail_analysis.py

Writes charts, results.json and report.md to analysis/output/.
"""

import json
import sys
from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.decomposition import PCA  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402
from statsmodels.tsa.seasonal import STL  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.analytics.forecast import SEASON, complete_series, forecast  # noqa: E402
from app.analytics.segmentation import segment  # noqa: E402

XLSX = ROOT / "data" / "online_retail_II.xlsx"
RAW = ROOT / "data" / "online_retail_II_2009_2011_raw.parquet"
OUT = ROOT / "analysis" / "output"
BLUE, ORANGE, GREY, DARK = "#1F5FAD", "#D9822B", "#9AA6B6", "#14213D"


def load() -> duckdb.DuckDBPyRelation:
    if not RAW.exists():
        sheets = pd.read_excel(XLSX, sheet_name=None, dtype={"Invoice": str, "StockCode": str})
        df = pd.concat(sheets.values(), ignore_index=True)
        df.columns = [
            "invoice",
            "stockcode",
            "description",
            "quantity",
            "invoicedate",
            "price",
            "customer_id",
            "country",
        ]
        duckdb.sql(f"COPY (SELECT * FROM df) TO '{RAW}' (FORMAT parquet)")
    return duckdb.sql(f"SELECT * FROM '{RAW}'")


def clean(raw: duckdb.DuckDBPyRelation):
    stats = {"raw_rows": raw.count("*").fetchone()[0]}
    dedup = duckdb.sql("SELECT DISTINCT * FROM raw")
    stats["after_dedup"] = dedup.count("*").fetchone()[0]
    sales = duckdb.sql("SELECT * FROM dedup WHERE invoice NOT LIKE 'C%' AND quantity > 0 AND price > 0")
    stats["sales_rows"] = sales.count("*").fetchone()[0]
    return sales, stats


def style(ax, title):
    ax.set_title(title, loc="left", fontsize=12, color=DARK, fontweight="bold")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#E5E7EB")
    ax.set_axisbelow(True)


def chart_k(seg, path):
    ks = [s["k"] for s in seg.k_scores]
    fig, ax1 = plt.subplots(figsize=(9, 4.5))
    ax1.plot(ks, [s["inertia"] for s in seg.k_scores], "o-", color=GREY, label="Inertia (elbow)")
    ax1.set_xlabel("Number of clusters k")
    ax1.set_ylabel("Inertia", color=GREY)
    ax2 = ax1.twinx()
    ax2.plot(ks, [s["silhouette"] for s in seg.k_scores], "o-", color=BLUE, label="Silhouette")
    ax2.set_ylabel("Silhouette score", color=BLUE)
    ax2.axvline(seg.k, color=ORANGE, ls="--")
    ax2.annotate(f"chosen k = {seg.k}", (seg.k, max(s["silhouette"] for s in seg.k_scores)), color=ORANGE)
    style(ax1, "Choosing k: elbow and silhouette")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def chart_clusters(seg, path):
    c = pd.DataFrame(seg.clusters)
    fig, ax = plt.subplots(figsize=(9, 4.5))
    y = np.arange(len(c))
    ax.barh(y - 0.2, c["customer_share_pct"], 0.4, color=GREY, label="Share of customers %")
    ax.barh(y + 0.2, c["revenue_share_pct"], 0.4, color=BLUE, label="Share of revenue %")
    ax.set_yticks(y, c["cluster"])
    ax.invert_yaxis()
    ax.legend(frameon=False)
    style(ax, f"K-means clusters (k = {seg.k}): customers vs revenue")
    ax.grid(axis="x", color="#E5E7EB")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def chart_scatter(seg, path):
    a = seg.assignments
    x = StandardScaler().fit_transform(np.log1p(a[["recency", "frequency", "monetary"]]))
    pc = PCA(n_components=2, random_state=42).fit(x)
    xy = pc.transform(x)
    fig, ax = plt.subplots(figsize=(9, 5))
    colors = [BLUE, ORANGE, "#2E8B57", "#8B5CF6", GREY, "#C2410C", "#0E7490", "#A16207", "#BE185D", "#4B5563"]
    for i, name in enumerate(sorted(a["cluster"].unique())):
        m = (a["cluster"] == name).to_numpy()
        ax.scatter(xy[m, 0], xy[m, 1], s=6, alpha=0.5, color=colors[i % len(colors)], label=name)
    ev = pc.explained_variance_ratio_ * 100
    ax.set_xlabel(f"PC1 ({ev[0]:.0f}% of variance)")
    ax.set_ylabel(f"PC2 ({ev[1]:.0f}% of variance)")
    ax.legend(frameon=False, markerscale=3, fontsize=8)
    style(ax, "Customers in RFM space (PCA projection)")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def chart_rfm(seg, path):
    r = pd.DataFrame(seg.rfm_segments)
    fig, ax = plt.subplots(figsize=(9, 4.8))
    y = np.arange(len(r))
    ax.barh(y - 0.2, r["customer_share_pct"], 0.4, color=GREY, label="Share of customers %")
    ax.barh(y + 0.2, r["revenue_share_pct"], 0.4, color=BLUE, label="Share of revenue %")
    ax.set_yticks(y, r["segment"])
    ax.invert_yaxis()
    ax.legend(frameon=False)
    style(ax, "RFM segments: customers vs revenue")
    ax.grid(axis="x", color="#E5E7EB")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def chart_forecast(fc, path, unit="£"):
    h = pd.DataFrame(fc.history)
    f = pd.DataFrame(fc.forecast)
    b = pd.DataFrame(fc.backtest)
    for d in (h, f, b):
        d["period"] = pd.to_datetime(d["period"])
    fig, ax = plt.subplots(figsize=(10, 4.8))
    ax.plot(h["period"], h["actual"], color=DARK, lw=1.6, label="Actual")
    ax.fill_between(
        f["period"], f["lower_95"].clip(lower=0), f["upper_95"], color=BLUE, alpha=0.15, label="95% interval"
    )
    ax.fill_between(
        f["period"], f["lower_80"].clip(lower=0), f["upper_80"], color=BLUE, alpha=0.25, label="80% interval"
    )
    ax.plot(f["period"], f["forecast"], color=BLUE, lw=2, label=f"Forecast: {fc.model}")
    ax.plot(b["period"], b["sarima"], color=ORANGE, lw=1.6, ls="--", label=f"Backtest: {fc.backtest_model}")
    if "seasonal_naive" in b:
        ax.plot(b["period"], b["seasonal_naive"], color=GREY, lw=1.4, ls=":", label="Backtest: seasonal naive")
    ax.yaxis.set_major_formatter(
        matplotlib.ticker.FuncFormatter(lambda v, _: f"{unit}{v / 1e6:.1f}M" if v >= 1e6 else f"{unit}{v / 1e3:.0f}k")
    )
    ax.legend(frameon=False, fontsize=8, ncol=2)
    style(ax, f"{fc.grain.title()}ly sales forecast, {fc.history_start} to {fc.forecast[-1]['period']}")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def chart_stl(y, s, path):
    res = STL(y, period=s, robust=True).fit()
    fig, axes = plt.subplots(4, 1, figsize=(10, 7), sharex=True)
    for ax, (name, series) in zip(
        axes, [("Observed", y), ("Trend", res.trend), ("Seasonal", res.seasonal), ("Residual", res.resid)]
    ):
        ax.plot(series.index, series.values, color=BLUE if name != "Residual" else GREY, lw=1.4)
        ax.set_ylabel(name)
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].set_title("STL decomposition of monthly sales", loc="left", fontsize=12, color=DARK, fontweight="bold")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def series(sales, grain):
    agg = duckdb.sql(
        f"SELECT date_trunc('{grain}', invoicedate) AS p, sum(quantity * price) AS v, "
        "min(invoicedate) AS a, max(invoicedate) AS b FROM sales GROUP BY 1"
    ).df()
    return complete_series(agg.set_index("p")["v"], grain, agg["a"].min(), agg["b"].max())


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sales, stats = clean(load())
    tx = duckdb.sql(
        "SELECT customer_id AS customer, invoice AS transaction, invoicedate AS date, quantity * price AS amount "
        "FROM sales WHERE customer_id IS NOT NULL"
    ).df()
    stats["sales_rows_with_customer"] = len(tx)

    seg = segment(tx)
    chart_k(seg, OUT / "seg_choose_k.png")
    chart_clusters(seg, OUT / "seg_clusters.png")
    chart_scatter(seg, OUT / "seg_pca_scatter.png")
    chart_rfm(seg, OUT / "seg_rfm_segments.png")

    forecasts = {}
    for grain in ("month", "week"):
        y, notes = series(sales, grain)
        fc = forecast(y, grain, notes=notes)
        forecasts[grain] = fc
        chart_forecast(fc, OUT / f"forecast_{grain}.png")
        if grain == "month":
            chart_stl(y, SEASON[grain], OUT / "forecast_stl_month.png")

    seg_out = {k: v for k, v in seg.__dict__.items() if k != "assignments"}
    results = {"data": stats, "segmentation": seg_out, "forecast": {g: f.__dict__ for g, f in forecasts.items()}}
    (OUT / "results.json").write_text(json.dumps(results, indent=1, default=str))
    (OUT / "report.md").write_text(report(stats, seg, forecasts))
    print(f"Wrote {OUT}")


def report(stats, seg, forecasts) -> str:
    lines = [
        "# Online Retail II: segmentation and forecasting",
        "",
        "## Data",
        f"- Raw rows (both sheets): {stats['raw_rows']:,}",
        f"- After removing exact duplicates (incl. the 1–9 Dec 2010 sheet overlap): {stats['after_dedup']:,}",
        f"- Sales rows (no cancellations, quantity > 0, price > 0): {stats['sales_rows']:,}",
        f"- Sales rows with a customer ID (used for segmentation): {stats['sales_rows_with_customer']:,}",
        "",
        "## Segmentation (RFM + K-means)",
        f"- Customers: {seg.customers:,}; as-of date {seg.as_of}",
        "- Features: log(1+x) of recency, frequency, monetary, standardised; K-means with 10 starts, seed 42",
        f"- Chosen k = {seg.k} (best silhouette for k ≥ 3); mean silhouette {seg.silhouette}",
        f"- Agreement between RFM segments and clusters (adjusted Rand index): {seg.agreement_ari}",
        "",
        "| k | Inertia | Silhouette |",
        "|---|---|---|",
        *[f"| {s['k']} | {s['inertia']:,} | {s['silhouette']} |" for s in seg.k_scores],
        "",
        "| Cluster | Customers | Customer % | Revenue % | Avg recency (days) | Avg orders | Avg spend £ | Silhouette |",
        "|---|---|---|---|---|---|---|---|",
        *[
            f"| {c['cluster']} | {c['customers']:,.0f} | {c['customer_share_pct']} | {c['revenue_share_pct']} | "
            f"{c['avg_recency_days']} | {c['avg_frequency']} | {c['avg_monetary']:,.0f} | {c['silhouette']} |"
            for c in seg.clusters
        ],
        "",
        "| RFM segment | Customers | Customer % | Revenue % |",
        "|---|---|---|---|",
        *[
            f"| {s['segment']} | {s['customers']:,.0f} | {s['customer_share_pct']} | {s['revenue_share_pct']} |"
            for s in seg.rfm_segments
        ],
        "",
        *[f"- Warning: {w}" for w in seg.warnings],
        "",
        "## Forecasting (SARIMA vs baselines)",
    ]
    for g, fc in forecasts.items():
        m = fc.metrics
        lines += [
            "",
            f"### {g.title()}ly",
            f"- History: {fc.history_periods} complete {g}s, {fc.history_start} to {fc.history_end}; "
            f"seasonal strength {fc.seasonal_strength}",
            f"- Final model: {fc.model} (AIC {fc.aic}); Ljung-Box p = {fc.ljung_box_p}",
            f"- Backtest: last {fc.backtest_periods} {g}s, model {fc.backtest_model} chosen on training data only",
            "",
            "| Model | MAPE % | sMAPE % | MAE £ | RMSE £ |",
            "|---|---|---|---|---|",
            *[
                f"| {name} | {v['mape_pct']} | {v['smape_pct']} | {v['mae']:,.0f} | {v['rmse']:,.0f} |"
                for name, v in m.items()
            ],
            "",
            f"- Improvement in MAE over seasonal naive: {fc.improvement_vs_seasonal_naive_pct}% · "
            f"confidence: **{fc.confidence}**",
            *[f"- Note: {w}" for w in fc.warnings],
        ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
