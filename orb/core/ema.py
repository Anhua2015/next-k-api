"""EMA 工具：聚合 K 线、趋势判定。"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import pandas as pd


def compute_ema_series(close: pd.Series, *, period: int) -> pd.Series:
    if close.empty or period < 1:
        return pd.Series(dtype=float)
    return close.astype(float).ewm(span=int(period), adjust=False).mean()


def aggregate_ohlcv(df: pd.DataFrame, bar_ms: int) -> pd.DataFrame:
    """按固定 bar_ms 聚合 OHLCV（open_time = bucket 起点）。"""
    cols = ["open_time", "open", "high", "low", "close", "volume"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    d = df.drop_duplicates(subset=["open_time"], keep="last").sort_values("open_time")
    buckets: Dict[int, dict] = {}
    for _, row in d.iterrows():
        ot = int(row["open_time"])
        key = (ot // int(bar_ms)) * int(bar_ms)
        o, h, l, c, v = (
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            float(row["volume"]),
        )
        if key not in buckets:
            buckets[key] = {
                "open_time": key,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": v,
            }
        else:
            b = buckets[key]
            b["high"] = max(b["high"], h)
            b["low"] = min(b["low"], l)
            b["close"] = c
            b["volume"] += v
    if not buckets:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(list(buckets.values())).sort_values("open_time").reset_index(drop=True)


def ema_at_bar_index(
    df: pd.DataFrame,
    idx: int,
    *,
    fast: int = 9,
    slow: int = 20,
) -> Optional[Tuple[float, float]]:
    """截至 idx（含）已完成 bar 的 EMA fast/slow。"""
    if df.empty or idx < 0 or idx >= len(df):
        return None
    sub = df.iloc[: idx + 1]
    if len(sub) < slow:
        return None
    close = sub["close"].astype(float)
    e9 = float(compute_ema_series(close, period=fast).iloc[-1])
    e20 = float(compute_ema_series(close, period=slow).iloc[-1])
    return e9, e20


def ema_values_asof(
    df: pd.DataFrame,
    asof_open_ms: int,
    *,
    fast: int = 9,
    slow: int = 20,
) -> Optional[Tuple[float, float]]:
    """截至 asof 前最后一根已完成 K 线（不含 forming bar）的 EMA。"""
    if df.empty:
        return None
    d = df.drop_duplicates(subset=["open_time"], keep="last").sort_values("open_time")
    completed = d[d["open_time"] <= int(asof_open_ms)]
    if len(completed) < slow:
        return None
    idx = len(completed) - 1
    return ema_at_bar_index(completed.reset_index(drop=True), idx, fast=fast, slow=slow)


def ema_trend_allows(side: str, ema9: float, ema20: float) -> bool:
    """9/20 方向过滤：多仅 9>20，空仅 9<20。"""
    side_u = str(side).upper()
    if side_u == "LONG":
        return ema9 > ema20
    if side_u == "SHORT":
        return ema9 < ema20
    return False


def bar_touches_ema_zone(
    *,
    high: float,
    low: float,
    ema9: float,
    ema20: float,
) -> bool:
    """价格区间与 9/20 EMA 带重叠（pullback）。"""
    z_lo = min(ema9, ema20)
    z_hi = max(ema9, ema20)
    return float(high) >= z_lo and float(low) <= z_hi
