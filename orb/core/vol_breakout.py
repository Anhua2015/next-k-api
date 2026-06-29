"""CrackingMarkets 式 ATR 波动率突破：前收 ± k×ATR 触发，k×ATR 止损，EOD 平仓。"""

from __future__ import annotations

import logging
from typing import FrozenSet, List, Optional, Set, Tuple

import pandas as pd

from orb.core.breakout import breakout_long as _breakout_long, breakout_short as _breakout_short, entry_price_for_side
from orb.core.config import OrbConfig
from orb.core.macro_calendar import is_macro_skip_day
from orb.core.session import session_anchor_ms, session_day_str, session_slice, trading_session_block_reason
from orb.core.signals import (
    OrbSignal,
    PreplaceArmBundle,
    _build_preplace_side_signal,
    compute_position_notional,
    compute_sl_tp,
    limit_price_for_side,
)

logger = logging.getLogger(__name__)


def is_vol_breakout_mode(cfg: OrbConfig) -> bool:
    return (cfg.entry_mode or "").strip().lower() == "vol_breakout"


def vb_skip_sides(session_sides_traded: Optional[Set[str]], cfg: OrbConfig) -> Optional[FrozenSet[str]]:
    """vol_breakout 多空各一次：已成交方向不再 preplace。"""
    if not is_vol_breakout_mode(cfg) or not getattr(cfg, "vb_one_attempt_per_side", False):
        return None
    if not session_sides_traded:
        return None
    return frozenset(str(s).upper() for s in session_sides_traded)


def prev_rth_close_asof(
    daily_df: pd.DataFrame,
    asof_open_ms: int,
    *,
    tz: str,
) -> Optional[float]:
    """截至 asof  session 日的前一 RTH 收盘价。"""
    if daily_df.empty:
        return None
    df = daily_df.drop_duplicates(subset=["open_time"], keep="last").sort_values("open_time")
    tz_name = tz or "UTC"
    asof_day = pd.Timestamp(int(asof_open_ms), unit="ms", tz=tz_name).normalize()
    day_ts = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_convert(tz_name).dt.normalize()
    completed = df[day_ts < asof_day]
    if completed.empty:
        return None
    val = float(completed["close"].iloc[-1])
    return val if val > 0 else None


def compute_vol_breakout_levels(
    *,
    prev_close: float,
    daily_atr: float,
    cfg: OrbConfig,
) -> Optional[Tuple[float, float, float]]:
    """返回 (upper, lower, width_pct)。"""
    k = float(getattr(cfg, "atr_breakout_mult", 0.0) or 0.0)
    if prev_close <= 0 or daily_atr <= 0 or k <= 0:
        return None
    band = float(daily_atr) * k
    upper = float(prev_close) + band
    lower = float(prev_close) - band
    width_pct = (upper - lower) / float(prev_close) * 100.0
    return upper, lower, width_pct


def vol_breakout_arm_ms(*, anchor_ms: int) -> int:
    """开盘即武装（首根 session K 线 open_time）。"""
    return int(anchor_ms)


def vol_breakout_ready_ms(*, anchor_ms: int) -> int:
    """should_arm_preplace 用：anchor 前 1ms 作为虚拟 or_end。"""
    return int(anchor_ms) - 1


def _resolve_levels(
    daily_df: pd.DataFrame,
    *,
    asof_open_ms: int,
    daily_atr: Optional[float],
    cfg: OrbConfig,
) -> Optional[Tuple[float, float, float, float, str]]:
    prev_close = prev_rth_close_asof(daily_df, int(asof_open_ms), tz=cfg.session_tz)
    if prev_close is None or daily_atr is None or daily_atr <= 0:
        return None
    pack = compute_vol_breakout_levels(prev_close=prev_close, daily_atr=daily_atr, cfg=cfg)
    if pack is None:
        return None
    upper, lower, width_pct = pack
    session_date = session_day_str(
        int(asof_open_ms), tz=cfg.session_tz, session_open_time=cfg.session_open_time
    )
    return upper, lower, width_pct, prev_close, session_date


def classify_vol_breakout_signal(
    symbol: str,
    df: pd.DataFrame,
    *,
    asof_open_ms: int,
    cfg: Optional[OrbConfig] = None,
    session_traded: bool = False,
    daily_atr: Optional[float] = None,
    daily_df: Optional[pd.DataFrame] = None,
    bot_equity_usdt: Optional[float] = None,
) -> OrbSignal:
    """1m 扫描：前收 ± k×ATR 突破确认后入场。"""
    c = cfg or OrbConfig.from_env()
    sym = str(symbol).strip().upper()
    flat = lambda reason: OrbSignal(sym, 0.0, "FLAT", "VB_NO_TRADE", "low", [reason])

    if df.empty:
        return flat("empty_df")
    if daily_df is None or daily_df.empty:
        return flat("daily_unavailable")
    if daily_atr is None or daily_atr <= 0:
        return flat("atr_unavailable")

    if (c.session_open_time or "").strip():
        anchor = session_anchor_ms(int(asof_open_ms), tz=c.session_tz, session_open_time=c.session_open_time)
        if int(asof_open_ms) < anchor:
            return flat("session_not_open")
    block = (
        trading_session_block_reason(
            int(asof_open_ms),
            tz=c.session_tz,
            session_open_time=c.session_open_time,
            session_close_time=c.session_close_time,
            market=c.market,
        )
        if c.regular_session_only
        else None
    )
    if block:
        return flat(block)

    resolved = _resolve_levels(daily_df, asof_open_ms=int(asof_open_ms), daily_atr=daily_atr, cfg=c)
    if resolved is None:
        return flat("levels_unavailable")
    upper, lower, width_pct, prev_close, session_date = resolved

    if c.macro_filter and is_macro_skip_day(session_date):
        return flat("macro_event_day")
    if c.one_trade_per_session and session_traded:
        return flat("session_already_traded")
    if c.min_or_width_pct > 0 and width_pct < c.min_or_width_pct:
        return flat("band_too_narrow")
    if c.max_or_width_pct > 0 and width_pct > c.max_or_width_pct:
        return flat("band_too_wide")
    if c.trade_window_minutes > 0:
        anchor = session_anchor_ms(int(asof_open_ms), tz=c.session_tz, session_open_time=c.session_open_time)
        if int(asof_open_ms) > anchor + int(c.trade_window_minutes) * 60_000:
            return flat("trade_window_expired")

    sess = session_slice(df, asof_open_ms, tz=c.session_tz, session_open_time=c.session_open_time)
    if len(sess) < 2:
        return flat("session_too_short")

    sess_pos = sess.reset_index(drop=True)
    matches = sess_pos.index[sess_pos["open_time"] == int(asof_open_ms)]
    if len(matches) == 0:
        return flat("bar_not_found")

    closes = sess_pos["close"].astype(float).tolist()
    k = float(c.atr_breakout_mult or 0)
    reasons = [
        f"prev_close={prev_close:.6f}",
        f"vb_upper={upper:.6f}",
        f"vb_lower={lower:.6f}",
        f"width={width_pct:.3f}%",
        f"atr={daily_atr:.6f}",
        f"k={k:.4f}",
        "mode=vol_breakout",
    ]

    long_ok = _breakout_long(closes, or_high=upper, confirm_bars=c.confirm_bars, no_soften=c.confirm_no_soften)
    short_ok = _breakout_short(closes, or_low=lower, confirm_bars=c.confirm_bars, no_soften=c.confirm_no_soften)
    if long_ok and short_ok:
        return OrbSignal(sym, float(closes[-1]), "FLAT", "VB_NO_TRADE", "low", reasons + ["ambiguous"])
    if not long_ok and not short_ok:
        return OrbSignal(sym, float(closes[-1]), "FLAT", "VB_NO_TRADE", "low", reasons + ["no_breakout"])

    side = "LONG" if long_ok else "SHORT"
    entry_px = entry_price_for_side(
        side=side,
        or_high=upper,
        or_low=lower,
        tick_size=c.tick_size,
        tick_offset=c.entry_tick_offset,
    )
    sl, tp, r_unit = compute_sl_tp(
        side=side, entry=entry_px, or_high=upper, or_low=lower, cfg=c, daily_atr=daily_atr
    )
    if sl is None:
        return OrbSignal(sym, entry_px, "FLAT", "VB_NO_TRADE", "low", reasons + ["sl_tp_failed"])

    play = f"VB_BREAKOUT_{side}"
    notion = compute_position_notional(entry=entry_px, sl=sl, cfg=c, bot_equity_usdt=bot_equity_usdt)
    return OrbSignal(
        symbol=sym,
        price=round(entry_px, 8),
        side=side,
        play=play,
        confidence="high",
        reasons=reasons + [f"signal_{side.lower()}"],
        or_high=round(upper, 8),
        or_low=round(lower, 8),
        or_mid=round(prev_close, 8),
        or_width_pct=round(width_pct, 4),
        session_date=session_date,
        entry_bar_open_ms=int(asof_open_ms),
        sl_price=sl,
        tp_price=tp,
        r_unit=r_unit,
        paper_notional_usdt=notion,
    )


def classify_vol_preplace_arm(
    symbol: str,
    df: pd.DataFrame,
    *,
    asof_open_ms: int,
    cfg: Optional[OrbConfig] = None,
    session_traded: bool = False,
    daily_atr: Optional[float] = None,
    daily_df: Optional[pd.DataFrame] = None,
    bot_equity_usdt: Optional[float] = None,
    now_ms: Optional[int] = None,
) -> OrbSignal:
    """开盘武装双 STOP：upper / lower = 前收 ± k×ATR。"""
    c = cfg or OrbConfig.from_env()
    sym = str(symbol).strip().upper()
    clock_ms = int(now_ms if now_ms is not None else asof_open_ms)

    def _flat(reason: str) -> OrbSignal:
        if reason in ("empty_df", "session_not_open", "atr_unavailable", "levels_unavailable"):
            logger.debug("[vb] preplace skip %s %s", sym, reason)
        else:
            logger.info("[vb] preplace skip %s %s session=%s", sym, reason, session_date if "session_date" in locals() else "")
        return OrbSignal(sym, 0.0, "FLAT", "VB_NO_TRADE", "low", [reason])

    if df.empty:
        return _flat("empty_df")
    if daily_df is None or daily_df.empty:
        return _flat("daily_unavailable")
    if daily_atr is None or daily_atr <= 0:
        return _flat("atr_unavailable")

    anchor = session_anchor_ms(int(asof_open_ms), tz=c.session_tz, session_open_time=c.session_open_time)
    if clock_ms < anchor:
        return _flat("session_not_open")
    block = (
        trading_session_block_reason(
            clock_ms,
            tz=c.session_tz,
            session_open_time=c.session_open_time,
            session_close_time=c.session_close_time,
            market=c.market,
        )
        if c.regular_session_only
        else None
    )
    if block:
        return _flat(block)

    resolved = _resolve_levels(daily_df, asof_open_ms=int(anchor), daily_atr=daily_atr, cfg=c)
    if resolved is None:
        return _flat("levels_unavailable")
    upper, lower, width_pct, prev_close, session_date = resolved
    ready_ms = vol_breakout_ready_ms(anchor_ms=int(anchor))
    arm_ms = vol_breakout_arm_ms(anchor_ms=int(anchor))

    if c.macro_filter and is_macro_skip_day(session_date):
        return _flat("macro_event_day")
    if clock_ms <= ready_ms:
        return _flat("session_not_open")
    if c.one_trade_per_session and session_traded:
        return _flat("session_already_traded")
    if c.min_or_width_pct > 0 and width_pct < c.min_or_width_pct:
        return _flat("band_too_narrow")
    if c.max_or_width_pct > 0 and width_pct > c.max_or_width_pct:
        return _flat("band_too_wide")
    if c.trade_window_minutes > 0 and clock_ms > arm_ms + int(c.trade_window_minutes) * 60_000:
        return _flat("trade_window_expired")

    k = float(c.atr_breakout_mult or 0)
    reasons = [
        f"prev_close={prev_close:.6f}",
        f"vb_upper={upper:.6f}",
        f"vb_lower={lower:.6f}",
        f"width={width_pct:.3f}%",
        f"atr={daily_atr:.6f}",
        f"k={k:.4f}",
        "mode=vol_preplace_arm",
        f"arm_ms={arm_ms}",
    ]

    long_entry = entry_price_for_side(
        side="LONG", or_high=upper, or_low=lower, tick_size=c.tick_size, tick_offset=c.entry_tick_offset
    )
    short_entry = entry_price_for_side(
        side="SHORT", or_high=upper, or_low=lower, tick_size=c.tick_size, tick_offset=c.entry_tick_offset
    )
    or_mid = float(prev_close)
    long_sig = _build_preplace_side_signal(
        sym=sym,
        side="LONG",
        entry_px=long_entry,
        or_high=upper,
        or_low=lower,
        or_mid=or_mid,
        width_pct=width_pct,
        session_date=session_date,
        or_end_ms=arm_ms,
        cfg=c,
        daily_atr=daily_atr,
        bot_equity_usdt=bot_equity_usdt,
        reasons=reasons,
    )
    short_sig = _build_preplace_side_signal(
        sym=sym,
        side="SHORT",
        entry_px=short_entry,
        or_high=upper,
        or_low=lower,
        or_mid=or_mid,
        width_pct=width_pct,
        session_date=session_date,
        or_end_ms=arm_ms,
        cfg=c,
        daily_atr=daily_atr,
        bot_equity_usdt=bot_equity_usdt,
        reasons=reasons,
    )
    if long_sig is None or short_sig is None:
        return _flat("sl_tp_failed")

    bundle = PreplaceArmBundle(long_sig=long_sig, short_sig=short_sig, or_end_ms=arm_ms)
    logger.info(
        "[vb] preplace arm %s session=%s upper=%.6f lower=%.6f width=%.3f%%",
        sym,
        session_date,
        upper,
        lower,
        width_pct,
    )
    return OrbSignal(
        symbol=sym,
        price=0.0,
        side="FLAT",
        play="VB_PREPLACE_ARM",
        confidence="high",
        reasons=reasons + ["preplace_arm_ready"],
        or_high=round(upper, 8),
        or_low=round(lower, 8),
        or_mid=round(or_mid, 8),
        or_width_pct=round(width_pct, 4),
        session_date=session_date,
        entry_bar_open_ms=int(arm_ms),
        preplace_arm=bundle,
    )
