"""Tier-A mom-turn spot lane — pure signal/exit helpers (no exchange I/O)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, Sequence

import numpy as np

ExitReason = Literal["stop", "tp1", "tp2", "trail20_after10", "timeout", ""]


@dataclass(frozen=True)
class TradePlan:
    entry: float
    stop: float
    tp1: float
    tp2: float
    trail_after_pct: float = 0.10
    trail_ema: int = 20


@dataclass(frozen=True)
class MomTurnSignal:
    side: int  # +1 long only
    entry: float
    stop: float
    tp1: float
    tp2: float
    ret_5: float
    signal: str


@dataclass(frozen=True)
class PositionState:
    entry: float
    stop: float
    tp1: float
    tp2: float
    qty_frac: float = 1.0
    tp1_hit: bool = False
    tp2_hit: bool = False
    trail_armed: bool = False
    bars_held: int = 0


def ema_last(close: np.ndarray, span: int) -> float:
    if len(close) < 1 or span < 1:
        return float("nan")
    alpha = 2.0 / (span + 1.0)
    val = float(close[0])
    for x in close[1:]:
        val = alpha * float(x) + (1.0 - alpha) * val
    return val


def ret_n(close: np.ndarray, n: int) -> float:
    if len(close) <= n or close[-1 - n] <= 0:
        return 0.0
    return 100.0 * (float(close[-1]) / float(close[-1 - n]) - 1.0)


def mom_turn_positive(close: np.ndarray, n: int = 5) -> bool:
    """5d return crosses from <=0 to >0 on the latest bar."""
    if len(close) < n + 2:
        return False
    now = ret_n(close, n)
    prev_base = float(close[-2 - n])
    if prev_base <= 0:
        return False
    prev = 100.0 * (float(close[-2]) / prev_base - 1.0)
    return bool(now > 0 and prev <= 0)


def reclaim_10ema(close: np.ndarray) -> bool:
    if len(close) < 12:
        return False
    e_now = ema_last(close, 10)
    e_prev = ema_last(close[:-1], 10)
    if np.isnan(e_now) or np.isnan(e_prev):
        return False
    c, prev_c = float(close[-1]), float(close[-2])
    return bool(c > e_now and prev_c <= e_prev)


def stop_price(entry: float, bar_low: float, stop_pct: float = 0.08) -> float:
    return max(float(entry) * (1.0 - float(stop_pct)), float(bar_low))


def trade_plan(
    entry: float,
    bar_low: float,
    *,
    stop_pct: float = 0.08,
    tp1: float = 0.30,
    tp2: float = 0.50,
) -> TradePlan:
    entry = float(entry)
    return TradePlan(
        entry=entry,
        stop=stop_price(entry, bar_low, stop_pct),
        tp1=entry * (1.0 + tp1),
        tp2=entry * (1.0 + tp2),
        trail_after_pct=0.10,
        trail_ema=20,
    )


def detect_mom_turn_signal(
    bars: Sequence[tuple],
    *,
    stop_pct: float = 0.08,
    allow_reclaim: bool = False,
    pool_ok: bool = True,
) -> MomTurnSignal | None:
    """
    bars: sequence of (ts_ms, open, high, low, close, volume), oldest→newest.
    Spot long-only. Returns None if pool gate closed or no trigger.
    """
    if not pool_ok or len(bars) < 30:
        return None
    close = np.array([float(b[4]) for b in bars], dtype=float)
    low = float(bars[-1][3])
    entry = float(bars[-1][4])
    signal: str | None = None
    if mom_turn_positive(close, 5):
        signal = "mom_turn_5d"
    elif allow_reclaim and reclaim_10ema(close):
        signal = "reclaim_10ema"
    if not signal:
        return None
    plan = trade_plan(entry, low, stop_pct=stop_pct)
    return MomTurnSignal(
        side=1,
        entry=plan.entry,
        stop=plan.stop,
        tp1=plan.tp1,
        tp2=plan.tp2,
        ret_5=round(ret_n(close, 5), 2),
        signal=signal,
    )


def on_bar_exit(
    state: PositionState,
    *,
    high: float,
    low: float,
    close: float,
    close_series: np.ndarray,
    trail_after_pct: float = 0.10,
    trail_ema: int = 20,
    max_hold_bars: int = 90,
) -> tuple[PositionState, float, ExitReason]:
    """
    Advance one completed bar for an open long.
    Returns (new_state, sell_frac, reason). sell_frac is fraction of *original* size to sell now.
    Stop has priority over TP / trail on the same bar (research-aligned).
    """
    st = replace(state, bars_held=state.bars_held + 1)
    sell = 0.0
    reason: ExitReason = ""

    if low <= st.stop and st.qty_frac > 0:
        return replace(st, qty_frac=0.0), st.qty_frac, "stop"

    if not st.tp1_hit and high >= st.tp1 and st.qty_frac > 0:
        chunk = min(st.qty_frac, 1.0 / 3.0)
        st = replace(st, qty_frac=st.qty_frac - chunk, tp1_hit=True)
        sell += chunk
        reason = "tp1"

    if not st.tp2_hit and high >= st.tp2 and st.qty_frac > 0:
        chunk = min(st.qty_frac, 1.0 / 3.0)
        st = replace(st, qty_frac=st.qty_frac - chunk, tp2_hit=True)
        sell += chunk
        reason = "tp2"

    gain = close / st.entry - 1.0 if st.entry > 0 else 0.0
    if gain >= trail_after_pct:
        st = replace(st, trail_armed=True)
    if st.trail_armed and st.qty_frac > 0 and len(close_series) >= trail_ema:
        e20 = ema_last(close_series, trail_ema)
        if not np.isnan(e20) and close < e20:
            chunk = st.qty_frac
            return replace(st, qty_frac=0.0), sell + chunk, "trail20_after10"

    if st.bars_held >= max_hold_bars and st.qty_frac > 0:
        chunk = st.qty_frac
        return replace(st, qty_frac=0.0), sell + chunk, "timeout"

    return st, sell, reason
