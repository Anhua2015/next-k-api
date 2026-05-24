"""Supertrend 开仓过滤（横盘减磨损）：ADX / HTF / 箱体 / 确认 K / 翻转冷却等。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

import supertrend_config as cfg
from supertrend_indicator import compute_supertrend


@dataclass(frozen=True)
class EntryFilterContext:
    symbol: str
    side: str  # LONG | SHORT
    st_df: pd.DataFrame
    close_px: float
    st_atr: float
    st_up: float
    st_dn: float
    trend: int
    bar_open_ms: int
    htf_trend: Optional[int] = None
    adx: Optional[float] = None
    range_pct: Optional[float] = None
    atr_pct: Optional[float] = None
    flip_count: int = 0
    vp_poc: Optional[float] = None
    vp_val: Optional[float] = None
    vp_vah: Optional[float] = None


def closed_bars_df(st_df: pd.DataFrame, *, timeframe_ms: int, now_ms: Optional[int] = None) -> pd.DataFrame:
    if st_df is None or st_df.empty or "open_time" not in st_df.columns:
        return pd.DataFrame()
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    close_times = st_df["open_time"].astype(np.int64) + int(timeframe_ms) - 1
    return st_df.loc[close_times <= now_ms].copy()


def compute_adx_series(df: pd.DataFrame, period: int) -> pd.Series:
    """Wilder ADX；与 st_df 同索引。"""
    if df is None or len(df) < period + 2:
        return pd.Series(dtype=float)
    high = df["high"].astype(float).to_numpy()
    low = df["low"].astype(float).to_numpy()
    close = df["close"].astype(float).to_numpy()
    n = len(close)
    up_move = np.zeros(n)
    dn_move = np.zeros(n)
    for i in range(1, n):
        up = high[i] - high[i - 1]
        dn = low[i - 1] - low[i]
        up_move[i] = up if up > dn and up > 0 else 0.0
        dn_move[i] = dn if dn > up and dn > 0 else 0.0
    tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
    tr[0] = high[0] - low[0]

    def wilder_smooth(x: np.ndarray, p: int) -> np.ndarray:
        out = np.full(n, np.nan)
        if n < p:
            return out
        out[p - 1] = np.sum(x[1:p])
        for i in range(p, n):
            out[i] = out[i - 1] - (out[i - 1] / p) + x[i]
        return out

    atr = wilder_smooth(tr, period)
    plus_dm = wilder_smooth(up_move, period)
    minus_dm = wilder_smooth(dn_move, period)
    plus_di = np.where(atr > 0, 100.0 * plus_dm / atr, 0.0)
    minus_di = np.where(atr > 0, 100.0 * minus_dm / atr, 0.0)
    denom = plus_di + minus_di
    with np.errstate(divide="ignore", invalid="ignore"):
        dx = np.where(denom > 0, 100.0 * np.abs(plus_di - minus_di) / denom, 0.0)
    dx = np.nan_to_num(dx, nan=0.0)
    adx = np.full(n, np.nan)
    start = period * 2 - 1
    if n <= start:
        return pd.Series(adx, index=df.index)
    adx[start] = np.nanmean(dx[period : start + 1])
    for i in range(start + 1, n):
        adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
    return pd.Series(adx, index=df.index)


def last_adx(closed: pd.DataFrame, period: int) -> Optional[float]:
    s = compute_adx_series(closed, period)
    if s.empty:
        return None
    v = s.iloc[-1]
    return None if pd.isna(v) else float(v)


def htf_trend_for_symbol(
    symbol: str,
    *,
    fetch_klines_fn,
    klines_to_df_fn,
    timeframe_ms: int,
) -> Optional[int]:
    tf = (cfg.ST_HTF_TIMEFRAME or "").strip()
    if not tf:
        return None
    rows = fetch_klines_fn(symbol, tf, cfg.ST_KLINE_LIMIT)
    if len(rows) < cfg.ST_ATR_PERIOD + 5:
        return None
    df = klines_to_df_fn(rows)
    st = compute_supertrend(
        df,
        period=cfg.ST_ATR_PERIOD,
        multiplier=cfg.ST_ATR_MULTIPLIER,
        source=cfg.ST_SOURCE,
        atr_method=cfg.ST_ATR_METHOD,
    )
    closed = closed_bars_df(st, timeframe_ms=timeframe_ms)
    if closed.empty:
        return None
    return int(closed.iloc[-1]["st_trend"])


def volume_profile_levels(
    closed: pd.DataFrame,
    lookback: int,
    *,
    num_bins: int = 42,
    value_area_pct: float = 0.70,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """近 lookback 根已收盘 K 的 POC / VAL / VAH（成交量分布价值区）。"""
    if closed.empty or lookback <= 0:
        return None, None, None
    tail = closed.tail(lookback)
    if tail.empty or "high" not in tail.columns or "low" not in tail.columns:
        return None, None, None
    vol_col = "volume" if "volume" in tail.columns else None
    if vol_col is None:
        return None, None, None

    lo = float(tail["low"].min())
    hi = float(tail["high"].max())
    if hi <= lo:
        mid = float(tail["close"].iloc[-1])
        return mid, lo, hi

    rows = [
        {
            "low": float(r["low"]),
            "high": float(r["high"]),
            "vol": float(r[vol_col]),
        }
        for _, r in tail.iterrows()
    ]
    tot_vol = sum(r["vol"] for r in rows)
    if tot_vol <= 0:
        mid = (lo + hi) / 2.0
        return mid, lo, hi

    n = max(int(num_bins), 12)
    step = (hi - lo) / float(n)
    if step <= 0:
        mid = (lo + hi) / 2.0
        return mid, lo, hi

    bins = [0.0] * n
    for r in rows:
        a, b, va = r["low"], r["high"], r["vol"]
        if va <= 0:
            continue
        ia = max(0, min(n - 1, int((a - lo) / step)))
        ib = max(0, min(n - 1, int((b - lo) / step)))
        if ia > ib:
            ia, ib = ib, ia
        span = ib - ia + 1
        per = va / float(span)
        for k in range(ia, ib + 1):
            bins[k] += per

    peak_i = max(range(n), key=lambda i: bins[i])
    poc = lo + (peak_i + 0.5) * step
    totv = sum(bins)
    if totv <= 0:
        return poc, lo, hi

    target = totv * float(value_area_pct)
    acc = bins[peak_i]
    L = R = peak_i
    while acc < target and (L > 0 or R < n - 1):
        lv = bins[L - 1] if L > 0 else -1.0
        rv = bins[R + 1] if R < n - 1 else -1.0
        if lv >= rv:
            if L > 0:
                L -= 1
                acc += bins[L]
            elif R < n - 1:
                R += 1
                acc += bins[R]
            else:
                break
        else:
            if R < n - 1:
                R += 1
                acc += bins[R]
            elif L > 0:
                L -= 1
                acc += bins[L]
            else:
                break
    val = lo + L * step
    vah = lo + (R + 1) * step
    return poc, val, vah


def range_pct(closed: pd.DataFrame, lookback: int) -> Optional[float]:
    if closed.empty or lookback <= 0:
        return None
    tail = closed.tail(lookback)
    if tail.empty:
        return None
    hi = float(tail["high"].max())
    lo = float(tail["low"].min())
    mid = float(tail["close"].iloc[-1])
    if mid <= 0:
        return None
    return (hi - lo) / mid


def bars_since_last_signal(closed: pd.DataFrame, column: str) -> int:
    """距最近一根信号 K 的偏移：0=当根，1=上一根，无信号=999。"""
    if closed.empty or column not in closed.columns:
        return 999
    flags = closed[column].astype(bool)
    for i in range(len(closed) - 1, -1, -1):
        if bool(flags.iloc[i]):
            return len(closed) - 1 - i
    return 999


def compute_entry_intent(
    *,
    trend: int,
    buy: bool,
    sell: bool,
    closed: pd.DataFrame,
    open_row: Optional[Any],
) -> Tuple[bool, bool]:
    """
    入场意图：flip 当根；window=0 时仅允许至多 ST_ENTRY_CONFIRM_BARS 根（无宽窗口补票）。
    """
    window = cfg.ST_ENTRY_WINDOW_BARS
    has_long = open_row is not None and str(open_row["side"]) == "LONG"
    has_short = open_row is not None and str(open_row["side"]) == "SHORT"

    since_buy = bars_since_last_signal(closed, "buy_signal")
    since_sell = bars_since_last_signal(closed, "sell_signal")

    def in_window(since: int) -> bool:
        if window <= 0:
            confirm_cap = cfg.ST_ENTRY_CONFIRM_BARS
            if confirm_cap <= 0:
                return since == 0
            return since <= confirm_cap
        return since <= window

    want_long = (
        not sell
        and not has_long
        and trend == 1
        and (buy or in_window(since_buy))
    )
    want_short = (
        not buy
        and not has_short
        and trend == -1
        and (sell or in_window(since_sell))
    )
    return want_long, want_short


def flip_signal_count(closed: pd.DataFrame, lookback: int) -> int:
    """买卖信号根数（偏激进）；防连斩请用 flip_trend_count。"""
    if closed.empty or lookback <= 0:
        return 0
    tail = closed.tail(lookback)
    buys = tail.get("buy_signal", pd.Series(False, index=tail.index)).astype(bool)
    sells = tail.get("sell_signal", pd.Series(False, index=tail.index)).astype(bool)
    return int((buys | sells).sum())


def flip_trend_count(closed: pd.DataFrame, lookback: int) -> int:
    """st_trend 方向切换次数（多空翻转）。"""
    if closed.empty or lookback <= 0 or "st_trend" not in closed.columns:
        return 0
    trends = closed.tail(lookback)["st_trend"].astype(int).tolist()
    if len(trends) < 2:
        return 0
    flips = 0
    prev = trends[0]
    for t in trends[1:]:
        if t != prev and t != 0 and prev != 0:
            flips += 1
        prev = t
    return flips


def entry_confirm_ok(closed: pd.DataFrame, side: str, bars: int) -> bool:
    if bars <= 0 or closed.empty:
        return True
    tail = closed.tail(bars)
    if len(tail) < bars:
        return False
    if side == "LONG":
        return bool((tail["st_trend"] == 1).all() and (tail["close"] > tail["st_up"]).all())
    if side == "SHORT":
        return bool((tail["st_trend"] == -1).all() and (tail["close"] < tail["st_dn"]).all())
    return False


def min_dist_ok(side: str, close_px: float, st_up: float, st_dn: float, st_atr: float) -> bool:
    mult = cfg.ST_MIN_DIST_ATR
    if mult <= 0 or st_atr <= 0:
        return True
    need = mult * st_atr
    if side == "LONG":
        return close_px - st_up >= need
    if side == "SHORT":
        return st_dn - close_px >= need
    return True


def build_filter_context(
    symbol: str,
    side: str,
    st_df: pd.DataFrame,
    last_bar: pd.Series,
    *,
    timeframe_ms: int,
    htf_trend: Optional[int] = None,
) -> EntryFilterContext:
    closed = closed_bars_df(st_df, timeframe_ms=timeframe_ms)
    close_px = float(last_bar["close"])
    st_atr = float(last_bar["st_atr"]) if not pd.isna(last_bar.get("st_atr")) else 0.0
    adx = last_adx(closed, cfg.ST_ADX_PERIOD) if cfg.ST_ADX_MIN > 0 else None
    rp = range_pct(closed, cfg.ST_RANGE_LOOKBACK) if cfg.ST_MAX_RANGE_PCT > 0 else None
    atr_pct = (st_atr / close_px) if close_px > 0 and st_atr > 0 else None
    flips = (
        flip_trend_count(closed, cfg.ST_CHOP_LOOKBACK)
        if cfg.ST_CHOP_MAX_FLIPS > 0
        else 0
    )
    vp_poc = vp_val = vp_vah = None
    if cfg.ST_FILTER_ENABLED and cfg.ST_VP_ENABLED and cfg.ST_VP_LOOKBACK > 0:
        vp_poc, vp_val, vp_vah = volume_profile_levels(
            closed,
            cfg.ST_VP_LOOKBACK,
            num_bins=cfg.ST_VP_NUM_BINS,
            value_area_pct=cfg.ST_VP_VALUE_AREA_PCT,
        )
    return EntryFilterContext(
        symbol=symbol,
        side=side,
        st_df=st_df,
        close_px=close_px,
        st_atr=st_atr,
        st_up=float(last_bar["st_up"]),
        st_dn=float(last_bar["st_dn"]),
        trend=int(last_bar["st_trend"]),
        bar_open_ms=int(last_bar["open_time"]),
        htf_trend=htf_trend,
        adx=adx,
        range_pct=rp,
        atr_pct=atr_pct,
        flip_count=flips,
        vp_poc=vp_poc,
        vp_val=vp_val,
        vp_vah=vp_vah,
    )


def _evaluate_vp_filters(ctx: EntryFilterContext) -> Tuple[bool, str]:
    if not cfg.ST_VP_ENABLED or cfg.ST_VP_LOOKBACK <= 0:
        return True, ""
    val, vah = ctx.vp_val, ctx.vp_vah
    if val is None or vah is None:
        return False, "vp_unavailable"
    px = ctx.close_px
    if val < px < vah:
        return False, "vp_inside_value_area"
    if ctx.side == "LONG" and px <= vah:
        return False, "vp_long_not_above_vah"
    if ctx.side == "SHORT" and px >= val:
        return False, "vp_short_not_below_val"
    return True, ""


def evaluate_entry_filters(ctx: EntryFilterContext) -> Tuple[bool, str]:
    """返回 (允许开仓, 拒绝原因 code)。"""
    if cfg.ST_VP_ENABLED and (cfg.ST_FILTER_ENABLED or cfg.ST_VP_INDEPENDENT):
        ok, reason = _evaluate_vp_filters(ctx)
        if not ok:
            return False, reason

    if not cfg.ST_FILTER_ENABLED:
        return True, ""

    side = ctx.side
    if cfg.ST_ADX_MIN > 0:
        if ctx.adx is None:
            return False, "adx_unavailable"
        if ctx.adx < cfg.ST_ADX_MIN:
            return False, f"adx_low:{ctx.adx:.1f}<{cfg.ST_ADX_MIN}"

    if cfg.ST_HTF_REQUIRE_ALIGN and (cfg.ST_HTF_TIMEFRAME or "").strip():
        if ctx.htf_trend is None:
            return False, "htf_unavailable"
        if side == "LONG" and ctx.htf_trend != 1:
            return False, "htf_not_bull"
        if side == "SHORT" and ctx.htf_trend != -1:
            return False, "htf_not_bear"

    if cfg.ST_MIN_ATR_PCT > 0:
        if ctx.atr_pct is None or ctx.atr_pct < cfg.ST_MIN_ATR_PCT:
            return False, "atr_pct_low"

    if cfg.ST_MAX_RANGE_PCT > 0:
        if ctx.range_pct is None:
            return False, "range_unavailable"
        if ctx.range_pct < cfg.ST_MAX_RANGE_PCT:
            return False, f"range_chop:{ctx.range_pct:.4f}"

    if cfg.ST_ENTRY_CONFIRM_BARS > 0:
        closed = closed_bars_df(ctx.st_df, timeframe_ms=cfg.st_timeframe_ms(cfg.ST_TIMEFRAME))
        if not entry_confirm_ok(closed, side, cfg.ST_ENTRY_CONFIRM_BARS):
            return False, "confirm_bars"

    if not min_dist_ok(side, ctx.close_px, ctx.st_up, ctx.st_dn, ctx.st_atr):
        return False, "min_dist"

    return True, ""


def chop_cooldown_until_bar(
    flip_count: int,
    bar_open_ms: int,
    timeframe_ms: int,
    *,
    filter_enabled: Optional[bool] = None,
) -> Optional[int]:
    """翻转过密时，返回禁止新开仓直到的 bar_open_ms（含）。"""
    if filter_enabled is None:
        filter_enabled = cfg.ST_FILTER_ENABLED
    if not filter_enabled:
        return None
    if cfg.ST_CHOP_MAX_FLIPS <= 0 or cfg.ST_CHOP_COOLDOWN_BARS <= 0:
        return None
    if flip_count < cfg.ST_CHOP_MAX_FLIPS:
        return None
    return bar_open_ms + cfg.ST_CHOP_COOLDOWN_BARS * timeframe_ms


def chop_cooldown_until_bar_from_ctx(ctx: EntryFilterContext, timeframe_ms: int) -> Optional[int]:
    return chop_cooldown_until_bar(ctx.flip_count, ctx.bar_open_ms, timeframe_ms)


def structure_sl_price(
    side: str,
    *,
    st_up: float,
    st_dn: float,
    prev_low: Optional[float] = None,
    prev_high: Optional[float] = None,
) -> Optional[float]:
    """
    结构硬止损价：多取 st_up 与前一根低点的较低者；空取 st_dn 与前一根高点的较高者。
    """
    if side == "LONG":
        candidates = [v for v in (st_up, prev_low) if v is not None and v > 0]
        return min(candidates) if candidates else None
    if side == "SHORT":
        candidates = [v for v in (st_dn, prev_high) if v is not None and v > 0]
        return max(candidates) if candidates else None
    return None


def structure_sl_valid(side: str, entry_price: float, sl_price: float) -> bool:
    if sl_price <= 0 or entry_price <= 0:
        return False
    if side == "LONG":
        return sl_price < entry_price
    if side == "SHORT":
        return sl_price > entry_price
    return False


def hard_sl_triggered(
    side: str,
    sl_price: Optional[float],
    *,
    low: float,
    high: float,
    close: float,
    use_wick: Optional[bool] = None,
) -> bool:
    if sl_price is None or sl_price <= 0:
        return False
    sl = float(sl_price)
    wick = cfg.ST_HARD_SL_USE_WICK if use_wick is None else bool(use_wick)
    if side == "LONG":
        return (low < sl) if wick else (close < sl)
    if side == "SHORT":
        return (high > sl) if wick else (close > sl)
    return False


def hard_sl_fill_price(
    side: str,
    sl_price: float,
    *,
    low: float,
    high: float,
    close: float,
) -> float:
    """纸面成交价：影线触发时用 SL 线价，否则收盘。"""
    sl = float(sl_price)
    if side == "LONG" and low < sl:
        return sl
    if side == "SHORT" and high > sl:
        return sl
    return close


def record_filter_reject(stats: Dict[str, Any], reason: str) -> None:
    key = (reason or "unknown").split(":")[0]
    rejects = stats.setdefault("filter_rejects", {})
    if isinstance(rejects, dict):
        rejects[key] = int(rejects.get(key, 0)) + 1
