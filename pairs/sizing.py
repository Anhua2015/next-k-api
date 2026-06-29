"""P_trace confidence sizing (Ruuj Ch5: scale exposure when filter is uncertain)."""

from __future__ import annotations

import pandas as pd


def p_trace_percentile_rank(p_trace: pd.Series, *, lookback: int = 252) -> pd.Series:
    """Rolling percentile rank of current P_trace within the window (0=low, 1=high)."""

    def _rank(window: pd.Series) -> float:
        v = float(window.iloc[-1])
        return float((window <= v).mean())

    return p_trace.rolling(lookback, min_periods=20).apply(_rank, raw=False).fillna(0.0)


def p_trace_entry_confident(
    p_trace: pd.Series,
    *,
    lookback: int = 252,
    halt_pct: float = 95.0,
) -> pd.Series:
    """True when P_trace is below rolling halt_pct quantile (safe to open new spread)."""
    thresh = p_trace.rolling(lookback, min_periods=20).quantile(halt_pct / 100.0)
    return (p_trace < thresh) | thresh.isna()


def p_trace_size_scale(
    p_trace: pd.Series,
    *,
    lookback: int = 252,
    min_scale: float = 0.25,
) -> pd.Series:
    """Linear scale in [min_scale, 1]: low P_trace -> full size, high -> reduced."""
    rank = p_trace_percentile_rank(p_trace, lookback=lookback)
    scale = 1.0 - rank * (1.0 - float(min_scale))
    return scale.clip(float(min_scale), 1.0)
