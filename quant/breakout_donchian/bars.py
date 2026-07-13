"""Kline / bar helpers for breakout_donchian (aligned with breakoutscanner)."""

from __future__ import annotations

import time
from typing import List, Sequence

import pandas as pd

from quant.market import klines_to_df

BarRow = tuple[int, float, float, float, float, float]

_INTERVAL_MS = {
    "1h": 3_600_000,
    "1d": 86_400_000,
    "1w": 604_800_000,
}


def drop_incomplete_bars(bars: Sequence[BarRow], interval: str) -> List[BarRow]:
    """Drop the in-progress candle (matches breakoutscanner semantics)."""
    rows = list(bars)
    if len(rows) < 2:
        return rows
    step = _INTERVAL_MS.get(interval.strip().lower())
    if not step:
        return rows
    now_ms = int(time.time() * 1000)
    last_ts = int(rows[-1][0])
    if now_ms < last_ts + step:
        return rows[:-1]
    return rows


def klines_df_to_bars(df) -> List[BarRow]:
    if df is None or df.empty:
        return []
    bars: List[BarRow] = []
    for _, row in df.iterrows():
        bars.append(
            (
                int(row["open_time"]),
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
                float(row["volume"]),
            )
        )
    return bars


def rows_to_df(bars: Sequence[BarRow]) -> pd.DataFrame:
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(bars, columns=["open_time", "open", "high", "low", "close", "volume"])
    df["dt"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df.set_index("dt").sort_index()


def resample_weekly_from_daily(daily_bars: Sequence[BarRow]) -> List[BarRow]:
    """W-SUN weekly bars — same rule as breakoutscanner/data_loader."""
    df = rows_to_df(daily_bars)
    if df.empty:
        return []
    weekly = df.resample("W-SUN").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    weekly = weekly.dropna(subset=["close"])
    out: List[BarRow] = []
    for ts, row in weekly.iterrows():
        out.append(
            (
                int(ts.timestamp() * 1000),
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
                float(row["volume"]),
            )
        )
    return drop_incomplete_bars(out, "1w")


def fetch_bars(
    sym: str,
    interval: str,
    *,
    days: int,
    exchange_id: str,
) -> List[BarRow]:
    from quant.market import fetch_klines_forward

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - max(1, days) * 86_400_000
    rows = fetch_klines_forward(sym, interval, start_ms, end_ms, exchange_id=exchange_id)
    bars = klines_df_to_bars(klines_to_df(rows))
    return drop_incomplete_bars(bars, interval)
