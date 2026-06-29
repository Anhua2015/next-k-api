"""Pair selection: cointegration + spread half-life (paper-style screening)."""

from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd


def _ols_spread(y: pd.Series, x: pd.Series) -> Tuple[float, float, pd.Series]:
    """OLS y ~ alpha + beta*x; return alpha, beta, residuals."""
    xa = np.asarray(x, dtype=float)
    ya = np.asarray(y, dtype=float)
    X = np.column_stack([np.ones(len(xa)), xa])
    coef, _, _, _ = np.linalg.lstsq(X, ya, rcond=None)
    alpha, beta = float(coef[0]), float(coef[1])
    resid = ya - (alpha + beta * xa)
    return alpha, beta, pd.Series(resid, index=y.index)


def adf_pvalue(series: pd.Series) -> float:
    """ADF p-value on spread; fallback if statsmodels missing."""
    try:
        from statsmodels.tsa.stattools import adfuller

        s = np.asarray(series, dtype=float)
        s = s[np.isfinite(s)]
        if len(s) < 30:
            return 1.0
        return float(adfuller(s, maxlag=1, regression="c", autolag=None)[1])
    except Exception:
        return _half_life_score(series)


def _half_life_score(spread: pd.Series) -> float:
    """Map half-life to pseudo p-value proxy (lower HL -> stronger mean reversion)."""
    s = spread.dropna()
    if len(s) < 30:
        return 1.0
    lag = s.shift(1)
    d = s.diff()
    df = pd.DataFrame({"lag": lag, "d": d}).dropna()
    if len(df) < 20:
        return 1.0
    beta = np.linalg.lstsq(df[["lag"]].values, df["d"].values, rcond=None)[0][0]
    if beta >= 0:
        return 1.0
    hl = -np.log(2.0) / beta
    if hl <= 0 or not np.isfinite(hl):
        return 1.0
    # half-life 5 bars -> ~0.01; 200 bars -> ~0.5
    return float(min(1.0, max(0.001, hl / 200.0)))


def spread_half_life_bars(spread: pd.Series) -> float:
    s = spread.dropna()
    if len(s) < 30:
        return float("inf")
    lag = s.shift(1)
    d = s.diff()
    df = pd.DataFrame({"lag": lag, "d": d}).dropna()
    beta = np.linalg.lstsq(df[["lag"]].values, df["d"].values, rcond=None)[0][0]
    if beta >= 0:
        return float("inf")
    hl = -np.log(2.0) / beta
    return float(hl) if np.isfinite(hl) and hl > 0 else float("inf")


def cointegration_stats(p1: pd.Series, p2: pd.Series, *, log_prices: bool = True) -> dict:
    """Engle-Granger style stats on aligned price series."""
    y = np.log(p1.astype(float)) if log_prices else p1.astype(float)
    x = np.log(p2.astype(float)) if log_prices else p2.astype(float)
    alpha, beta, resid = _ols_spread(y, x)
    adf_p = adf_pvalue(resid)
    hl = spread_half_life_bars(resid)
    corr = float(y.corr(x)) if len(y) > 1 else 0.0
    return {
        "alpha": round(alpha, 6),
        "beta": round(beta, 6),
        "adf_pvalue": round(adf_p, 4),
        "half_life_bars": round(hl, 1) if np.isfinite(hl) else None,
        "log_corr": round(corr, 4),
        "cointegrated_5pct": adf_p < 0.05,
    }
