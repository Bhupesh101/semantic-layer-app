"""Customer segmentation: RFM scoring and K-means clustering (P11-01..P11-11)."""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score, silhouette_samples, silhouette_score
from sklearn.preprocessing import StandardScaler

SEED = 42

# Classic RFM segment map on (R score, F score), first match wins (P11-04).
SEGMENT_RULES = [
    (r"5[4-5]", "Champions"),
    (r"[3-4][4-5]", "Loyal Customers"),
    (r"[4-5][2-3]", "Potential Loyalists"),
    (r"51", "New Customers"),
    (r"41", "Promising"),
    (r"33", "Need Attention"),
    (r"3[1-2]", "About to Sleep"),
    (r"[1-2]5", "Can't Lose Them"),
    (r"[1-2][3-4]", "At Risk"),
    (r"[1-2][1-2]", "Hibernating"),
]


@dataclass
class SegmentationResult:
    customers: int
    as_of: str
    rfm_segments: List[Dict[str, Any]]
    k_scores: List[Dict[str, Any]]
    k: int
    silhouette: float
    clusters: List[Dict[str, Any]]
    agreement_ari: float
    crosstab: Dict[str, Dict[str, int]]
    warnings: List[str] = field(default_factory=list)
    # Per-customer assignments stay local: never sent to the LLM (P11-13, NFR-08)
    assignments: Optional[pd.DataFrame] = None


def rfm_table(tx: pd.DataFrame, as_of: Optional[pd.Timestamp] = None) -> pd.DataFrame:
    """tx columns: customer, transaction, date, amount (one row per transaction or line)."""
    tx = tx.dropna(subset=["customer"])
    as_of = as_of or (tx["date"].max() + pd.Timedelta(days=1))
    g = tx.groupby("customer")
    rfm = pd.DataFrame(
        {
            "recency": (as_of - g["date"].max()).dt.days,
            "frequency": g["transaction"].nunique(),
            "monetary": g["amount"].sum(),
        }
    )
    return rfm[rfm["monetary"] > 0]


def _quintile(s: pd.Series, reverse: bool = False) -> pd.Series:
    # rank first so ties never collapse a bin (P11-03)
    q = pd.qcut(s.rank(method="first"), 5, labels=[1, 2, 3, 4, 5]).astype(int)
    return 6 - q if reverse else q


def rfm_scores(rfm: pd.DataFrame) -> pd.DataFrame:
    out = rfm.copy()
    out["r"] = _quintile(out["recency"], reverse=True)
    out["f"] = _quintile(out["frequency"])
    out["m"] = _quintile(out["monetary"])
    rf = out["r"].astype(str) + out["f"].astype(str)
    out["segment"] = rf.map(lambda key: next(name for pat, name in SEGMENT_RULES if re.fullmatch(pat, key)))
    return out


def _profile(df: pd.DataFrame, by: str, total_revenue: float) -> List[Dict[str, Any]]:
    g = df.groupby(by)
    prof = pd.DataFrame(
        {
            "customers": g.size(),
            "avg_recency_days": g["recency"].mean(),
            "avg_frequency": g["frequency"].mean(),
            "avg_monetary": g["monetary"].mean(),
            "revenue": g["monetary"].sum(),
        }
    )
    prof["customer_share_pct"] = 100 * prof["customers"] / len(df)
    prof["revenue_share_pct"] = 100 * prof["revenue"] / total_revenue
    prof = prof.sort_values("revenue", ascending=False).round(1)
    return [{by: str(idx), **{k: _py(v) for k, v in row.items()}} for idx, row in prof.iterrows()]


def _py(v: Any) -> Any:
    return v.item() if isinstance(v, np.generic) else v


def _level(v: float, lo: float, hi: float, words) -> str:
    return words[0] if v <= lo else words[2] if v > hi else words[1]


def _cluster_name(c: pd.Series, lo: pd.Series, hi: pd.Series) -> str:
    """Describe a centroid against the customer-level tertiles of each feature."""
    spend = _level(c["monetary"], lo["monetary"], hi["monetary"], ("Low spend", "Mid spend", "High spend"))
    freq = _level(c["frequency"], lo["frequency"], hi["frequency"], ("rare", "regular", "frequent"))
    act = _level(c["recency"], lo["recency"], hi["recency"], ("active", "cooling", "lapsed"))
    return f"{spend}, {freq}, {act}"


def segment(tx: pd.DataFrame, k: Optional[int] = None, k_range=range(2, 11)) -> SegmentationResult:
    rfm = rfm_scores(rfm_table(tx))
    n = len(rfm)
    if n < 20:
        raise ValueError(f"Only {n} customers with positive spend; segmentation needs at least 20.")
    warnings = []
    one_time = float((rfm["frequency"] == 1).mean() * 100)
    if one_time > 40:
        warnings.append(f"{one_time:.0f}% of customers bought only once, so frequency separates them weakly.")
    skew = float(rfm["monetary"].skew())
    if skew > 5:
        warnings.append(f"Spend is very skewed (skewness {skew:.0f}); features are log-transformed before clustering.")

    x = StandardScaler().fit_transform(np.log1p(rfm[["recency", "frequency", "monetary"]]))
    sample = min(n, 5000)
    k_scores = []
    models = {}
    for kk in k_range:
        if kk >= n:
            break
        km = KMeans(n_clusters=kk, n_init=10, random_state=SEED).fit(x)
        models[kk] = km
        sil = silhouette_score(x, km.labels_, sample_size=sample, random_state=SEED)
        k_scores.append({"k": kk, "inertia": round(float(km.inertia_), 1), "silhouette": round(float(sil), 3)})
    if k is None:
        # k=2 usually just splits high from low; suggest the best k >= 3 for actionable segments (P11-08)
        candidates = [s for s in k_scores if s["k"] >= 3] or k_scores
        k = max(candidates, key=lambda s: s["silhouette"])["k"]
    km = models.get(k) or KMeans(n_clusters=k, n_init=10, random_state=SEED).fit(x)
    labels = km.labels_
    sil_all = silhouette_samples(x, labels)
    rfm["cluster_id"] = labels
    feats = rfm[["recency", "frequency", "monetary"]]
    lo, hi = feats.quantile(1 / 3), feats.quantile(2 / 3)
    names = {}
    for cid, c in rfm.groupby("cluster_id")[["recency", "frequency", "monetary"]].mean().iterrows():
        base = _cluster_name(c, lo, hi)
        names[cid] = base if base not in names.values() else f"{base} ({cid})"
    rfm["cluster"] = rfm["cluster_id"].map(names)

    total = float(rfm["monetary"].sum())
    clusters = _profile(rfm, "cluster", total)
    for c in clusters:
        mask = rfm["cluster"] == c["cluster"]
        c["silhouette"] = round(float(sil_all[mask.to_numpy()].mean()), 3)
    ct = pd.crosstab(rfm["segment"], rfm["cluster"])
    return SegmentationResult(
        customers=n,
        as_of=str((tx["date"].max() + pd.Timedelta(days=1)).date()),
        rfm_segments=_profile(rfm, "segment", total),
        k_scores=k_scores,
        k=int(k),
        silhouette=round(float(sil_all.mean()), 3),
        clusters=clusters,
        agreement_ari=round(float(adjusted_rand_score(rfm["segment"], rfm["cluster"])), 3),
        crosstab={seg: {cl: int(v) for cl, v in row.items() if v} for seg, row in ct.iterrows()},
        warnings=warnings,
        assignments=rfm,
    )
