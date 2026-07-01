"""与 vnpy ArrayManager 对齐的简易指标。"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(int(n), min_periods=int(n)).mean()


def std(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(int(n), min_periods=int(n)).std(ddof=0)


def ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=int(n), adjust=False, min_periods=int(n)).mean()


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([(h - l).abs(), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.rolling(int(n), min_periods=int(n)).mean()


def boll(close: pd.Series, n: int, dev: float) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = sma(close, n)
    sd = std(close, n)
    up = mid + float(dev) * sd
    down = mid - float(dev) * sd
    return up, down


def cci(df: pd.DataFrame, n: int) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    ma = tp.rolling(int(n), min_periods=int(n)).mean()
    md = (tp - ma).abs().rolling(int(n), min_periods=int(n)).mean()
    return (tp - ma) / (0.015 * md.replace(0, np.nan))


def rsi(close: pd.Series, n: int) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0)
    down = (-delta).clip(lower=0)
    roll_up = up.ewm(alpha=1 / int(n), adjust=False).mean()
    roll_down = down.ewm(alpha=1 / int(n), adjust=False).mean()
    rs = roll_up / roll_down.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def keltner(df: pd.DataFrame, n: int, dev: float) -> tuple[pd.Series, pd.Series]:
    """与 vnpy ArrayManager.keltner 一致：SMA(close) ± dev * ATR。"""
    mid = sma(df["close"], n)
    a = atr(df, n)
    up = mid + float(dev) * a
    down = mid - float(dev) * a
    return up, down


def donchian_high(high: pd.Series, n: int) -> pd.Series:
    return high.rolling(int(n), min_periods=int(n)).max()


def donchian_low(low: pd.Series, n: int) -> pd.Series:
    return low.rolling(int(n), min_periods=int(n)).min()
