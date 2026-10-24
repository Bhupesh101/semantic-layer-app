"""Time-series forecasting with SARIMA, backtested against naive baselines (P12-01..P12-12)."""

import itertools
import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from statsmodels.stats.diagnostic import acorr_ljungbox
from statsmodels.tsa.seasonal import STL
from statsmodels.tsa.statespace.sarimax import SARIMAX
from statsmodels.tsa.stattools import adfuller, kpss

FREQ = {"day": "D", "week": "W-MON", "month": "MS"}
SEASON = {"day": 7, "week": 52, "month": 12}  # P12-05
HORIZON = {"day": 14, "week": 12, "month": 6}
HOLDOUT = {"day": 28, "week": 13, "month": 6}


@dataclass
class ForecastResult:
    grain: str
    model: str
    history_periods: int
    history_start: str
    history_end: str
    order: Tuple[int, int, int]
    seasonal_order: Tuple[int, int, int, int]
    drift: bool
    seasonal_strength: Optional[float]
    aic: float
    ljung_box_p: Optional[float]
    backtest_periods: int
    backtest_model: str
    metrics: Dict[str, Dict[str, Optional[float]]]
    beats_seasonal_naive: Optional[bool]
    improvement_vs_seasonal_naive_pct: Optional[float]
    confidence: str
    forecast: List[Dict[str, Any]]
    backtest: List[Dict[str, Any]]
    history: List[Dict[str, Any]]
    warnings: List[str] = field(default_factory=list)


def complete_series(
    s: pd.Series, grain: str, data_start: pd.Timestamp, data_end: pd.Timestamp
) -> Tuple[pd.Series, List[str]]:
    """Fill missing periods with 0 and drop partial first/last periods (P12-02)."""
    notes = []
    s = s.sort_index()
    s.index = pd.DatetimeIndex(s.index)
    full = pd.date_range(s.index.min(), s.index.max(), freq=FREQ[grain])
    filled = s.reindex(full, fill_value=0.0).astype(float)
    gaps = int(len(full) - len(s))
    if gaps:
        notes.append(f"{gaps} period(s) with no data were filled with 0.")
    step = pd.tseries.frequencies.to_offset(FREQ[grain])
    if filled.index[0] < data_start.normalize():
        filled = filled.iloc[1:]
        notes.append("The first period was incomplete and was dropped.")
    if filled.index[-1] + step > data_end.normalize() + pd.Timedelta(days=1):
        filled = filled.iloc[:-1]
        notes.append("The last period was incomplete and was dropped.")
    return filled, notes


def _choose_d(y: pd.Series) -> int:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            adf_p = adfuller(y, autolag="AIC")[1]
            kpss_p = kpss(y, regression="c", nlags="auto")[1]
        except (ValueError, np.linalg.LinAlgError):
            return 1
    return 0 if adf_p < 0.05 and kpss_p > 0.05 else 1


def seasonal_strength(y: pd.Series, s: int) -> Optional[float]:
    """Hyndman's STL seasonal strength, 0..1; None when there are fewer than two cycles."""
    if len(y) < 2 * s:
        return None
    res = STL(y, period=s, robust=True).fit()
    return float(max(0.0, 1 - np.var(res.resid) / np.var(res.seasonal + res.resid)))


def _fit(y: pd.Series, order, seasonal_order, trend: str = "n"):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return SARIMAX(
            y,
            order=order,
            seasonal_order=seasonal_order,
            trend=trend,
            enforce_stationarity=False,
            enforce_invertibility=False,
        ).fit(disp=False)


def effective_obs(m) -> int:
    """Observations the likelihood actually uses after differencing and diffuse initialisation."""
    return int(m.nobs - m.loglikelihood_burn)


def select_orders(y: pd.Series, s: int, D: int) -> Tuple[tuple, tuple, str, float]:
    """AIC search over (p,d,q)(P,D,Q,s) and a drift term, with d from ADF/KPSS (P12-06).

    Seasonal AR/MA terms need two full cycles left after seasonal differencing; otherwise only
    seasonal differencing is used. Fits left with too few effective observations are rejected,
    because their likelihood (and so their AIC) is computed on almost no data.
    """
    d = 0 if D else _choose_d(y)
    room = len(y) - (D or 0) * s
    seasonal_terms = [(0, 0), (0, 1), (1, 0), (1, 1)] if D is not None and room >= 2 * s else [(0, 0)]
    trends = {0: ["c"], 1: ["n", "c"]}.get(d + (D or 0), ["n"])
    best = None
    for (P, Q), p, q, trend in itertools.product(seasonal_terms, range(3), range(3), trends):
        so = (P, D, Q, s) if D is not None else (0, 0, 0, 0)
        try:
            m = _fit(y, (p, d, q), so, trend)
        except (ValueError, np.linalg.LinAlgError):
            continue
        if effective_obs(m) < 2 * len(m.params) + 2:  # about two observations per parameter
            continue
        if np.isfinite(m.aic) and (best is None or m.aic < best[3]):
            best = ((p, d, q), so, trend, float(m.aic))
    if best is None:
        raise ValueError("No SARIMA model could be fitted to this series.")
    return best


def model_label(order, seasonal_order, trend: str) -> str:
    p, d, q = order
    P, D, Q, s = seasonal_order
    label = f"SARIMA({p},{d},{q})({P},{D},{Q},{s})" if s else f"ARIMA({p},{d},{q})"
    return label + (" with drift" if trend == "c" else "")


def _metrics(actual: np.ndarray, pred: np.ndarray) -> Dict[str, Optional[float]]:
    err = actual - pred
    nz = actual != 0
    denom = np.abs(actual) + np.abs(pred)
    return {
        "mape_pct": round(float(np.mean(np.abs(err[nz] / actual[nz])) * 100), 1) if nz.any() else None,
        "smape_pct": round(float(np.mean(np.where(denom == 0, 0, 2 * np.abs(err) / denom)) * 100), 1),
        "mae": round(float(np.mean(np.abs(err))), 1),
        "rmse": round(float(np.sqrt(np.mean(err**2))), 1),
    }


def _confidence(mape: Optional[float], beats: Optional[bool], lb_p: Optional[float], small_backtest: bool) -> str:
    """P12-12: good / fair / poor from backtest error and residual checks; a small backtest caps it at fair."""
    if mape is None:
        return "poor"
    if mape <= 10 and beats is not False and (lb_p is None or lb_p > 0.05) and not small_backtest:
        return "good"
    if mape <= 25 and beats is not False:
        return "fair"
    return "poor"


def forecast(
    y: pd.Series, grain: str, horizon: Optional[int] = None, notes: Optional[List[str]] = None
) -> ForecastResult:
    """y: a complete, evenly spaced series (see complete_series)."""
    horizon = horizon or HORIZON[grain]
    s = SEASON[grain]
    warn = list(notes or [])
    n = len(y)
    if n < 12:
        raise ValueError(f"Only {n} complete periods of history; forecasting needs at least 12.")
    strength = seasonal_strength(y, s)  # P12-03: None below two full cycles
    if strength is None:
        warn.append(f"Only {n} {grain}s of history; seasonal models need {2 * s}. Using a non-seasonal ARIMA instead.")
        D = None
    else:
        # nsdiffs-style rule: seasonal differencing when seasonal strength > 0.64
        D = 1 if strength > 0.64 else 0

    # Backtest on a holdout (P12-10) against naive and seasonal-naive (P12-11).
    # Orders are chosen on the training part only, so the test periods never leak into selection.
    h = min(HOLDOUT[grain], max(2, n // 5))
    train, test = y.iloc[:-h], y.iloc[-h:]
    D_bt = D if D is not None and len(train) >= s + 8 else None
    bt_order, bt_so, bt_trend, _ = select_orders(train, s, D_bt)
    bt_model = _fit(train, bt_order, bt_so, bt_trend)
    pred = bt_model.get_forecast(h).predicted_mean.values
    small_backtest = h < 6 or effective_obs(bt_model) < 20
    if small_backtest:
        warn.append(
            f"The backtest is small ({h} test periods, model fitted on {effective_obs(bt_model)} effective "
            "periods), so its accuracy figures are indicative only."
        )
    order, so, trend, aic = select_orders(y, s, D)
    naive = np.repeat(train.values[-1], h)
    metrics = {"sarima": _metrics(test.values, pred), "naive": _metrics(test.values, naive)}
    snaive = None
    if n > s + h:
        snaive = y.shift(s).iloc[-h:].values
        metrics["seasonal_naive"] = _metrics(test.values, snaive)
    beats, gain = None, None
    if snaive is not None:
        beats = metrics["sarima"]["mae"] < metrics["seasonal_naive"]["mae"]
        gain = round(100 * (1 - metrics["sarima"]["mae"] / metrics["seasonal_naive"]["mae"]), 1)
        if not beats:
            warn.append("SARIMA did not beat the seasonal-naive baseline in the backtest; treat its forecast as weak.")

    model = _fit(y, order, so, trend)
    burn = order[1] + so[1] * so[3]
    resid = model.resid.iloc[burn:]
    lb_p = None
    if len(resid) > 10:
        lb_p = round(float(acorr_ljungbox(resid, lags=[min(10, len(resid) // 5)])["lb_pvalue"].iloc[0]), 3)
        if lb_p < 0.05:
            warn.append(f"Residuals still show autocorrelation (Ljung-Box p = {lb_p}); intervals may be too narrow.")
    fc = model.get_forecast(horizon)
    mean, ci80, ci95 = fc.predicted_mean, fc.conf_int(alpha=0.2), fc.conf_int(alpha=0.05)
    rows = [
        {
            "period": str(idx.date()),
            "forecast": round(float(mean.iloc[i]), 1),
            "lower_80": round(float(ci80.iloc[i, 0]), 1),
            "upper_80": round(float(ci80.iloc[i, 1]), 1),
            "lower_95": round(float(ci95.iloc[i, 0]), 1),
            "upper_95": round(float(ci95.iloc[i, 1]), 1),
        }
        for i, idx in enumerate(mean.index)
    ]
    if any(r["lower_95"] < 0 for r in rows) and (y >= 0).all():
        warn.append("Some lower interval bounds are below zero; the metric cannot be negative, so read them as 0.")
    backtest = [
        {
            "period": str(idx.date()),
            "actual": round(float(test.iloc[i]), 1),
            "sarima": round(float(pred[i]), 1),
            **({"seasonal_naive": round(float(snaive[i]), 1)} if snaive is not None else {}),
        }
        for i, idx in enumerate(test.index)
    ]
    return ForecastResult(
        grain=grain,
        model=model_label(order, so, trend),
        history_periods=n,
        history_start=str(y.index[0].date()),
        history_end=str(y.index[-1].date()),
        order=tuple(int(v) for v in order),
        seasonal_order=tuple(int(v) for v in so),
        drift=trend == "c",
        seasonal_strength=None if strength is None else round(strength, 2),
        aic=round(aic, 1),
        ljung_box_p=lb_p,
        backtest_periods=h,
        backtest_model=model_label(bt_order, bt_so, bt_trend),
        metrics=metrics,
        beats_seasonal_naive=beats,
        improvement_vs_seasonal_naive_pct=gain,
        confidence=_confidence(metrics["sarima"]["mape_pct"], beats, lb_p, small_backtest),
        forecast=rows,
        backtest=backtest,
        history=[{"period": str(i.date()), "actual": round(float(v), 1)} for i, v in y.items()],
        warnings=warn,
    )
