"""Kalman filter for dynamic hedge ratio (pairs / stat arb)."""

from __future__ import annotations

from typing import Tuple, Union

import numpy as np
import pandas as pd

ArrayLike = Union[pd.Series, np.ndarray, list]


def kalman_hedge_ratio(
    p1: ArrayLike,
    p2: ArrayLike,
    *,
    delta: float = 1e-4,
    r_noise: float = 1.0,
) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]:
    """Estimate time-varying beta, intercept, spread, forecast error e, innovation variance S, P trace.

    State x = [beta, mu]. Observation y_t = p1_t ~ beta * p2_t + mu.
    """
    if isinstance(p1, pd.Series):
        index = p1.index
    elif isinstance(p2, pd.Series):
        index = p2.index
    else:
        index = pd.RangeIndex(len(p1))

    a1 = np.asarray(p1, dtype=float)
    a2 = np.asarray(p2, dtype=float)
    n = len(a1)
    if len(a2) != n:
        raise ValueError("p1 and p2 must have the same length")

    if r_noise <= 0 or r_noise == 1.0:
        # Scale observation noise to price level (avoid tiny S → huge z on crypto)
        diff_var = float(np.var(np.diff(a1[: min(252, n)]))) if n > 2 else 1.0
        r = max(diff_var, (float(np.mean(a1[: min(20, n)])) * 1e-4) ** 2, 1e-8)
    else:
        r = float(r_noise)

    state = np.array([1.0, 0.0])
    p_mat = np.eye(2)
    q = (float(delta) / (1.0 - float(delta))) * np.eye(2) if delta < 1.0 else np.eye(2) * 1e-4

    beta = np.zeros(n)
    intercept = np.zeros(n)
    e = np.zeros(n)
    s = np.zeros(n)
    p_trace = np.zeros(n)

    for t in range(n):
        h = np.array([a2[t], 1.0])
        p_mat = p_mat + q
        e[t] = a1[t] - float(h @ state)
        s[t] = float(h @ p_mat @ h) + r
        k = (p_mat @ h) / s[t] if s[t] > 0 else np.zeros(2)
        state = state + k * e[t]
        p_mat = (np.eye(2) - np.outer(k, h)) @ p_mat
        beta[t] = state[0]
        intercept[t] = state[1]
        p_trace[t] = float(np.trace(p_mat))

    spread = a1 - beta * a2 - intercept
    return (
        pd.Series(beta, index=index, name="beta"),
        pd.Series(intercept, index=index, name="intercept"),
        pd.Series(spread, index=index, name="spread"),
        pd.Series(e, index=index, name="e"),
        pd.Series(s, index=index, name="S"),
        pd.Series(p_trace, index=index, name="P_trace"),
    )


def kalman_zscore(e: ArrayLike, s: ArrayLike) -> pd.Series:
    """Innovation z-score: e / sqrt(S)."""
    e_s = pd.Series(e, dtype=float)
    s_s = pd.Series(s, dtype=float)
    denom = np.sqrt(s_s.clip(lower=1e-12))
    return (e_s / denom).rename("zscore")
