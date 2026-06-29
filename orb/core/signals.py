"""ORB 量价信号：突破 / 回踩 + 成交量确认。"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import pandas as pd

from orb.core.breakout import breakout_long as _breakout_long, breakout_short as _breakout_short, entry_price_for_side
from orb.core.config import OrbConfig
from orb.core.macro_calendar import is_macro_skip_day
from orb.core.session import (
    compute_opening_range,
    session_anchor_ms,
    session_slice,
    trading_session_block_reason,
)

logger = logging.getLogger(__name__)

_PREPLACE_SKIP_DEBUG = frozenset(
    {"empty_df", "session_not_open", "or_window_in_progress", "atr_unavailable"}
)


def _or_width_in_exclude_band(width_pct: float) -> bool:
    """可选：ORB_OR_WIDTH_EXCLUDE_LO/HI 排除 OR 宽度区间（如 1.5–2% 死区）。"""
    lo = float(os.getenv("ORB_OR_WIDTH_EXCLUDE_LO") or 0)
    hi = float(os.getenv("ORB_OR_WIDTH_EXCLUDE_HI") or 0)
    if lo <= 0 or hi <= lo:
        return False
    return lo <= float(width_pct) < hi


@dataclass
class OrbSignal:
    symbol: str
    price: float
    side: str
    play: str
    confidence: str
    reasons: List[str] = field(default_factory=list)
    or_high: float = 0.0
    or_low: float = 0.0
    or_mid: float = 0.0
    or_width_pct: float = 0.0
    session_date: str = ""
    entry_bar_open_ms: Optional[int] = None
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    r_unit: Optional[float] = None
    paper_notional_usdt: Optional[float] = None
    volume: float = 0.0
    vol_ma: float = 0.0
    preplace_arm: Optional["PreplaceArmBundle"] = None

    @property
    def regime(self) -> str:
        return "ORB"


@dataclass
class PreplaceArmBundle:
    """OR 收盘武装：双挂 STOP（OCO），无 5m 突破确认。"""

    long_sig: OrbSignal
    short_sig: OrbSignal
    or_end_ms: int


def limit_price_for_side(*, entry: float, side: str, cfg: OrbConfig) -> float:
    chase = max(0, int(cfg.max_chase_ticks)) * float(cfg.tick_size)
    if str(side).upper() == "LONG":
        return round(float(entry) + chase, 8)
    return round(float(entry) - chase, 8)


def worst_fill_for_preplace(*, stop_entry: float, side: str, cfg: OrbConfig) -> float:
    """Preplace 保守定价：STOP 触发价 + 限价追价上限 = 最差成交价。"""
    return limit_price_for_side(entry=stop_entry, side=side, cfg=cfg)


def preplace_sl_risk_dist(*, stop_entry: float, sl: float) -> float:
    """STOP 触发价与 SL 的距离（atr 模式 fill 后平移 SL 用）。"""
    return abs(float(sl) - float(stop_entry))


def sl_on_loss_side(*, side: str, entry: float, sl: float) -> bool:
    """SL 是否在亏损侧（与 Protocol validate_sl 方向一致）。"""
    side_u = str(side).upper()
    e, s = float(entry), float(sl)
    if side_u == "LONG":
        return s < e
    if side_u == "SHORT":
        return s > e
    return False


def refresh_preplace_leg_after_fill(
    leg: OrbSignal,
    *,
    fill_px: float,
    cfg: OrbConfig,
    daily_atr: Optional[float] = None,
    bot_equity_usdt: Optional[float] = None,
) -> OrbSignal:
    """成交后按真实 fill 重算 SL/仓位；定仓仍用 worst-fill。"""
    from dataclasses import replace

    side_u = str(leg.side).upper()
    fill = round(float(fill_px), 8)
    stop_px = round(float(leg.price), 8)
    or_h = float(leg.or_high or 0)
    or_l = float(leg.or_low or 0)
    sl, tp, r_unit = compute_sl_tp(
        side=side_u,
        entry=fill,
        or_high=or_h,
        or_low=or_l,
        cfg=cfg,
        daily_atr=daily_atr,
    )
    if sl is None:
        return replace(leg, price=fill)
    worst = worst_fill_for_preplace(stop_entry=stop_px, side=side_u, cfg=cfg)
    notion = compute_position_notional(
        entry=worst,
        sl=sl,
        cfg=cfg,
        bot_equity_usdt=bot_equity_usdt,
        for_preplace=True,
        or_width_pct=float(leg.or_width_pct or 0),
    )
    return replace(
        leg,
        price=fill,
        sl_price=sl,
        tp_price=tp,
        r_unit=r_unit,
        paper_notional_usdt=round(notion, 4),
    )


def effective_risk_pct(cfg: OrbConfig, *, for_preplace: bool = False) -> float:
    """Preplace 可选缩放 risk_pct（默认 1.0；position_safety_pct 已留安全垫）。"""
    base = max(0.0, float(cfg.risk_pct or 0.0))
    if not for_preplace:
        return base
    explicit = float(getattr(cfg, "preplace_risk_pct", 0.0) or 0.0)
    if explicit > 0:
        return explicit
    scale = float(getattr(cfg, "preplace_risk_scale", 1.0) or 1.0)
    return base * scale


def risk_pct_for_or_width(
    cfg: OrbConfig,
    or_width_pct: float,
    *,
    for_preplace: bool = False,
) -> float:
    """OR 宽度分区 risk：ORB_RISK_PCT_ZONE_LO/HI + ORB_RISK_PCT_IN_ZONE。"""
    base = effective_risk_pct(cfg, for_preplace=for_preplace)
    lo = float(os.getenv("ORB_RISK_PCT_ZONE_LO") or 0)
    hi = float(os.getenv("ORB_RISK_PCT_ZONE_HI") or 0)
    zone = float(os.getenv("ORB_RISK_PCT_IN_ZONE") or 0)
    w = float(or_width_pct or 0)
    if lo > 0 and hi > lo and zone > 0 and lo <= w < hi:
        return zone
    return base


def first_post_or_bar_open_ms(*, or_end_ms: int) -> int:
    """OR 窗口最后一毫秒之后的第一根 K 线 open_time。"""
    return int(or_end_ms) + 1


def or_end_ms_for_anchor(*, anchor_ms: int, cfg: OrbConfig) -> int:
    return int(anchor_ms) + max(1, int(cfg.or_minutes)) * 60_000 - 1


def should_arm_preplace(*, now_ms: int, or_end_ms: int) -> bool:
    """时钟已过 OR 结束即可武装（不等 post-OR 5m 收盘）。"""
    return int(now_ms) > int(or_end_ms)


def _build_preplace_side_signal(
    *,
    sym: str,
    side: str,
    entry_px: float,
    or_high: float,
    or_low: float,
    or_mid: float,
    width_pct: float,
    session_date: str,
    or_end_ms: int,
    cfg: OrbConfig,
    daily_atr: Optional[float],
    bot_equity_usdt: Optional[float],
    reasons: List[str],
) -> Optional[OrbSignal]:
    side_u = str(side).upper()
    stop_px = round(float(entry_px), 8)
    worst_px = worst_fill_for_preplace(stop_entry=stop_px, side=side_u, cfg=cfg)
    # SL/TP 锚定 STOP 触发价；定仓仍用 worst-fill（保守）
    sl, tp, r_unit = compute_sl_tp(
        side=side_u,
        entry=stop_px,
        or_high=or_high,
        or_low=or_low,
        cfg=cfg,
        daily_atr=daily_atr,
    )
    if sl is None:
        return None
    if (cfg.exit_mode or "").strip().lower() != "eod" and tp is None:
        return None
    notion = compute_position_notional(
        entry=worst_px, sl=sl, cfg=cfg, bot_equity_usdt=bot_equity_usdt, for_preplace=True,
        or_width_pct=float(width_pct),
    )
    return OrbSignal(
        symbol=sym,
        price=stop_px,
        side=side_u,
        play=f"ORB_PREPLACE_{side_u}",
        confidence="high",
        reasons=list(reasons)
        + [
            f"preplace_arm_{side_u.lower()}",
            f"stop_entry={stop_px:.6f}",
            f"worst_entry={worst_px:.6f}",
            f"risk_pct={effective_risk_pct(cfg, for_preplace=True):.4f}",
        ],
        or_high=round(or_high, 8),
        or_low=round(or_low, 8),
        or_mid=round(or_mid, 8),
        or_width_pct=round(width_pct, 4),
        session_date=session_date,
        entry_bar_open_ms=int(or_end_ms),
        sl_price=sl,
        tp_price=tp,
        r_unit=r_unit,
        paper_notional_usdt=round(notion, 4),
    )


def classify_or_preplace_arm(
    symbol: str,
    df: pd.DataFrame,
    *,
    asof_open_ms: int,
    cfg: Optional[OrbConfig] = None,
    session_traded: bool = False,
    daily_atr: Optional[float] = None,
    bot_equity_usdt: Optional[float] = None,
    now_ms: Optional[int] = None,
) -> OrbSignal:
    """OR 收盘武装：前置过滤 + 双 STOP，不做 5m 突破确认。"""
    c = cfg or OrbConfig.from_env()
    sym = str(symbol).strip().upper()
    clock_ms = int(now_ms if now_ms is not None else asof_open_ms)

    def _flat(reason: str) -> OrbSignal:
        sess_date = session_date if "session_date" in locals() else ""
        if reason in _PREPLACE_SKIP_DEBUG:
            logger.debug("[orb] preplace skip %s %s or=%dm", sym, reason, int(c.or_minutes))
        elif reason in ("session_too_short", "or_not_ready", "sl_tp_failed", "bar_not_found"):
            logger.warning(
                "[orb] preplace skip %s %s or=%dm session=%s bars=%s need=%s",
                sym,
                reason,
                int(c.or_minutes),
                sess_date,
                len(sess) if "sess" in locals() else "?",
                min_sess_bars if "min_sess_bars" in locals() else "?",
            )
        else:
            logger.info(
                "[orb] preplace skip %s %s or=%dm session=%s",
                sym,
                reason,
                int(c.or_minutes),
                sess_date,
            )
        return OrbSignal(sym, 0.0, "FLAT", "ORB_NO_TRADE", "low", [reason])

    if df.empty:
        return _flat("empty_df")
    if (c.session_open_time or "").strip():
        anchor = session_anchor_ms(int(clock_ms), tz=c.session_tz, session_open_time=c.session_open_time)
        if int(clock_ms) < anchor:
            return _flat("session_not_open")
    block = (
        trading_session_block_reason(
            int(clock_ms),
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

    probe_ms = int(asof_open_ms)
    sess = session_slice(
        df, probe_ms, tz=c.session_tz, session_open_time=c.session_open_time
    )
    step_min = max(1, int(c.bar_step_ms()) // 60_000)
    min_sess_bars = max(1, (int(c.or_minutes) + step_min - 1) // step_min)
    if len(sess) < min_sess_bars:
        return _flat("session_too_short")

    pack = compute_opening_range(
        sess,
        or_minutes=c.or_minutes,
        bar_step_ms=c.bar_step_ms(),
        asof_open_ms=probe_ms,
        tz=c.session_tz,
        session_open_time=c.session_open_time,
    )
    if not pack:
        return _flat("or_not_ready")

    or_high = float(pack["or_high"])
    or_low = float(pack["or_low"])
    width_pct = float(pack["or_width_pct"])
    or_end_ms = int(pack["or_end_ms"])
    arm_ms = first_post_or_bar_open_ms(or_end_ms=or_end_ms)
    session_date = str(pack["session_date"])

    if c.macro_filter and is_macro_skip_day(session_date):
        return _flat("macro_event_day")
    if not should_arm_preplace(now_ms=clock_ms, or_end_ms=or_end_ms):
        return _flat("or_window_in_progress")
    if c.one_trade_per_session and session_traded:
        return _flat("session_already_traded")
    if (c.sl_mode or "").strip().lower() == "atr_pct" and daily_atr is None:
        return _flat("atr_unavailable")
    if c.min_or_width_pct > 0 and width_pct < c.min_or_width_pct:
        return _flat("or_too_narrow")
    if c.max_or_width_pct > 0 and width_pct > c.max_or_width_pct:
        return _flat("or_too_wide")
    if _or_width_in_exclude_band(width_pct):
        return _flat("or_width_exclude_band")
    if c.trade_window_minutes > 0:
        if int(clock_ms) > or_end_ms + int(c.trade_window_minutes) * 60_000:
            return _flat("trade_window_expired")

    sess_pos = sess.reset_index(drop=True)
    or_rows = sess_pos[sess_pos["open_time"] <= or_end_ms]
    if or_rows.empty:
        return _flat("bar_not_found")
    bar_idx = int(or_rows.index[-1])
    vol_ok, vol, vma = _volume_ok(sess_pos, bar_idx, period=c.vol_ma_period, mult=c.vol_mult)
    if c.vol_mult > 0 and not vol_ok:
        return _flat("volume_filter")

    vwap = _session_vwap(sess_pos)
    reasons = [
        f"or_h={or_high:.6f}",
        f"or_l={or_low:.6f}",
        f"width={width_pct:.3f}%",
        f"vol={vol:.2f}/ma={vma:.2f}",
        f"vwap={vwap:.6f}",
        f"mode=preplace_arm",
        f"or_end_ms={or_end_ms}",
        f"arm_ms={arm_ms}",
        f"clock_ms={clock_ms}",
    ]
    if daily_atr:
        reasons.append(f"atr={daily_atr:.6f}")

    long_entry = entry_price_for_side(
        side="LONG",
        or_high=or_high,
        or_low=or_low,
        tick_size=c.tick_size,
        tick_offset=c.entry_tick_offset,
    )
    short_entry = entry_price_for_side(
        side="SHORT",
        or_high=or_high,
        or_low=or_low,
        tick_size=c.tick_size,
        tick_offset=c.entry_tick_offset,
    )
    long_sig = _build_preplace_side_signal(
        sym=sym,
        side="LONG",
        entry_px=long_entry,
        or_high=or_high,
        or_low=or_low,
        or_mid=float(pack["or_mid"]),
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
        or_high=or_high,
        or_low=or_low,
        or_mid=float(pack["or_mid"]),
        width_pct=width_pct,
        session_date=session_date,
        or_end_ms=arm_ms,
        cfg=c,
        daily_atr=daily_atr,
        bot_equity_usdt=bot_equity_usdt,
        reasons=reasons,
    )
    if long_sig is None or short_sig is None:
        logger.warning(
            "[orb] preplace skip %s sl_tp_failed or=%dm session=%s or_h=%.6f or_l=%.6f",
            sym,
            int(c.or_minutes),
            session_date,
            or_high,
            or_low,
        )
        return OrbSignal(sym, 0.0, "FLAT", "ORB_NO_TRADE", "low", reasons + ["sl_tp_failed"])

    bundle = PreplaceArmBundle(long_sig=long_sig, short_sig=short_sig, or_end_ms=arm_ms)
    long_limit = limit_price_for_side(entry=long_sig.price, side="LONG", cfg=c)
    short_limit = limit_price_for_side(entry=short_sig.price, side="SHORT", cfg=c)
    logger.info(
        "[orb] preplace arm ready %s session=%s or=%dm width=%.3f%% "
        "L stop=%.6f lim=%.6f sl=%.6f notion=%.2f | "
        "S stop=%.6f lim=%.6f sl=%.6f notion=%.2f arm_ms=%s",
        sym,
        session_date,
        int(c.or_minutes),
        width_pct,
        float(long_sig.price),
        long_limit,
        float(long_sig.sl_price or 0),
        float(long_sig.paper_notional_usdt or 0),
        float(short_sig.price),
        short_limit,
        float(short_sig.sl_price or 0),
        float(short_sig.paper_notional_usdt or 0),
        arm_ms,
    )
    return OrbSignal(
        symbol=sym,
        price=0.0,
        side="FLAT",
        play="ORB_PREPLACE_ARM",
        confidence="high",
        reasons=reasons + ["preplace_arm_ready"],
        or_high=round(or_high, 8),
        or_low=round(or_low, 8),
        or_mid=round(float(pack["or_mid"]), 8),
        or_width_pct=round(width_pct, 4),
        session_date=session_date,
        entry_bar_open_ms=int(arm_ms),
        volume=round(vol, 4),
        vol_ma=round(vma, 4),
        preplace_arm=bundle,
    )


def _volume_ok(df: pd.DataFrame, idx: int, *, period: int, mult: float) -> Tuple[bool, float, float]:
    if mult <= 0 or period <= 1 or idx < 0:
        vol = float(df["volume"].iloc[idx]) if len(df) else 0.0
        return True, vol, vol
    start = max(0, idx - period + 1)
    window = df.iloc[start : idx + 1]
    vma = float(window["volume"].mean()) if len(window) else 0.0
    vol = float(window["volume"].iloc[-1]) if len(window) else 0.0
    ok = vol >= vma * mult if vma > 0 else True
    return ok, vol, vma


def _session_vwap(sess: pd.DataFrame) -> float:
    if sess.empty:
        return 0.0
    typical = (
        sess["high"].astype(float) + sess["low"].astype(float) + sess["close"].astype(float)
    ) / 3.0
    vol = sess["volume"].astype(float)
    total = float(vol.sum())
    if total <= 0:
        return float(sess["close"].iloc[-1])
    return float((typical * vol).sum() / total)


def _had_volume_breakout_long(
    sess: pd.DataFrame, or_high: float, *, period: int, mult: float
) -> bool:
    if len(sess) < 2:
        return False
    for i in range(len(sess) - 1):
        if float(sess["close"].iloc[i]) <= or_high:
            continue
        ok, _, _ = _volume_ok(sess, i, period=period, mult=mult)
        if ok:
            return True
    return False


def _had_volume_breakout_short(
    sess: pd.DataFrame, or_low: float, *, period: int, mult: float
) -> bool:
    if len(sess) < 2:
        return False
    for i in range(len(sess) - 1):
        if float(sess["close"].iloc[i]) >= or_low:
            continue
        ok, _, _ = _volume_ok(sess, i, period=period, mult=mult)
        if ok:
            return True
    return False


def _retest_long(sess: pd.DataFrame, or_high: float, *, tol_pct: float = 0.05) -> bool:
    if len(sess) < 3:
        return False
    closes = sess["close"].astype(float).tolist()
    lows = sess["low"].astype(float).tolist()
    if not any(c > or_high for c in closes[:-1]):
        return False
    tol = or_high * tol_pct / 100.0
    return lows[-1] <= or_high + tol and closes[-1] > or_high


def _retest_short(sess: pd.DataFrame, or_low: float, *, tol_pct: float = 0.05) -> bool:
    if len(sess) < 3:
        return False
    closes = sess["close"].astype(float).tolist()
    highs = sess["high"].astype(float).tolist()
    if not any(c < or_low for c in closes[:-1]):
        return False
    tol = or_low * tol_pct / 100.0
    return highs[-1] >= or_low - tol and closes[-1] < or_low


def compute_position_notional(
    *,
    entry: float,
    sl: float,
    cfg: OrbConfig,
    bot_equity_usdt: Optional[float] = None,
    for_preplace: bool = False,
    risk_pct_override: Optional[float] = None,
    or_width_pct: Optional[float] = None,
) -> float:
    """固定名义优先；否则按单标机器人本金的风险百分比定仓。"""
    fixed = float(getattr(cfg, "fixed_notional_usdt", 0.0) or 0.0)
    if fixed > 0:
        return fixed
    equity = float(bot_equity_usdt if bot_equity_usdt is not None else cfg.per_symbol_bot_equity())
    if equity <= 0 or not cfg.uses_risk_sizing() or entry <= 0 or sl <= 0:
        return float(cfg.default_paper_notional())
    risk_frac = abs(entry - sl) / entry
    if risk_frac <= 0:
        return float(cfg.default_paper_notional())
    if risk_pct_override is not None:
        risk_pct = float(risk_pct_override)
    elif or_width_pct is not None:
        risk_pct = risk_pct_for_or_width(cfg, float(or_width_pct), for_preplace=for_preplace)
    else:
        risk_pct = effective_risk_pct(cfg, for_preplace=for_preplace)
    budget = equity * risk_pct * (1.0 - cfg.position_safety_pct)
    return budget / risk_frac


def _enforce_min_sl(
    *,
    side: str,
    entry: float,
    sl: float,
    risk: float,
    cfg: OrbConfig,
) -> Tuple[float, float]:
    """可选下限；默认 0=关闭，不改动论文 5%×ATR 止损。"""
    min_pct = float(getattr(cfg, "min_sl_pct", 0.0) or 0.0)
    if min_pct <= 0 or entry <= 0:
        return sl, risk
    min_risk = entry * min_pct
    if risk >= min_risk:
        return sl, risk
    side_u = str(side).upper()
    if side_u == "LONG":
        return entry - min_risk, min_risk
    return entry + min_risk, min_risk


def compute_sl_tp(
    *,
    side: str,
    entry: float,
    or_high: float,
    or_low: float,
    cfg: OrbConfig,
    daily_atr: Optional[float] = None,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if entry <= 0 or or_high <= or_low:
        return None, None, None
    side_u = str(side).upper()
    sl_mode = (cfg.sl_mode or "or_range").strip().lower()

    if sl_mode == "atr_pct":
        if daily_atr is None or daily_atr <= 0 or cfg.atr_sl_fraction <= 0:
            return None, None, None
        stop_dist = float(daily_atr) * float(cfg.atr_sl_fraction)
        if side_u == "LONG":
            sl = entry - stop_dist
            risk = stop_dist
        else:
            sl = entry + stop_dist
            risk = stop_dist
        if risk <= 0:
            return None, None, None
        sl, risk = _enforce_min_sl(side=side_u, entry=entry, sl=sl, risk=risk, cfg=cfg)
        if (cfg.exit_mode or "").strip().lower() == "eod":
            tp = None
        else:
            tp = entry + float(cfg.tp_r_multiple) * risk if side_u == "LONG" else entry - float(cfg.tp_r_multiple) * risk
        return round(sl, 8), round(tp, 8) if tp is not None else None, round(risk, 8)

    buf = entry * float(cfg.sl_buffer_bps) / 10_000.0
    tick = float(getattr(cfg, "tick_size", 0.01) or 0.01)
    if side_u == "LONG":
        sl = float(or_low) - max(buf, tick)
        risk = entry - sl
    else:
        sl = float(or_high) + max(buf, tick)
        risk = sl - entry
    if risk <= 0:
        return None, None, None
    sl, risk = _enforce_min_sl(side=side_u, entry=entry, sl=sl, risk=risk, cfg=cfg)
    tp = entry + float(cfg.tp_r_multiple) * risk if side_u == "LONG" and cfg.tp_r_multiple > 0 else None
    if side_u == "SHORT" and cfg.tp_r_multiple > 0:
        tp = entry - float(cfg.tp_r_multiple) * risk
    if (cfg.exit_mode or "").strip().lower() == "eod":
        tp = None
    return round(sl, 8), round(tp, 8) if tp is not None else None, round(risk, 8)


def classify_signal(
    symbol: str,
    df: pd.DataFrame,
    *,
    asof_open_ms: int,
    cfg: Optional[OrbConfig] = None,
    session_traded: bool = False,
    daily_atr: Optional[float] = None,
    bot_equity_usdt: Optional[float] = None,
) -> OrbSignal:
    c = cfg or OrbConfig.from_env()
    sym = str(symbol).strip().upper()
    flat = lambda reason: OrbSignal(sym, 0.0, "FLAT", "ORB_NO_TRADE", "low", [reason])

    if df.empty:
        return flat("empty_df")
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

    sess = session_slice(
        df, asof_open_ms, tz=c.session_tz, session_open_time=c.session_open_time
    )
    if len(sess) < 3:
        return flat("session_too_short")

    pack = compute_opening_range(
        sess,
        or_minutes=c.or_minutes,
        bar_step_ms=c.bar_step_ms(),
        asof_open_ms=int(asof_open_ms),
        tz=c.session_tz,
        session_open_time=c.session_open_time,
    )
    if not pack:
        return flat("or_not_ready")

    or_high = float(pack["or_high"])
    or_low = float(pack["or_low"])
    width_pct = float(pack["or_width_pct"])
    or_end_ms = int(pack["or_end_ms"])
    session_date = str(pack["session_date"])

    if c.macro_filter and is_macro_skip_day(session_date):
        return flat("macro_event_day")
    if int(asof_open_ms) <= or_end_ms:
        return flat("or_window_in_progress")
    if c.one_trade_per_session and session_traded:
        return flat("session_already_traded")
    if c.min_or_width_pct > 0 and width_pct < c.min_or_width_pct:
        return flat("or_too_narrow")
    if c.max_or_width_pct > 0 and width_pct > c.max_or_width_pct:
        return flat("or_too_wide")
    if _or_width_in_exclude_band(width_pct):
        return flat("or_width_exclude_band")
    if c.trade_window_minutes > 0:
        if int(asof_open_ms) > or_end_ms + int(c.trade_window_minutes) * 60_000:
            return flat("trade_window_expired")

    sess_pos = sess.reset_index(drop=True)
    matches = sess_pos.index[sess_pos["open_time"] == int(asof_open_ms)]
    if len(matches) == 0:
        return flat("bar_not_found")
    bar_idx = int(matches[0])
    closes = sess_pos["close"].astype(float).tolist()
    entry_px = float(sess_pos["close"].iloc[-1])
    vwap = _session_vwap(sess_pos)
    vol_ok, vol, vma = _volume_ok(sess_pos, bar_idx, period=c.vol_ma_period, mult=c.vol_mult)
    reasons = [
        f"or_h={or_high:.6f}",
        f"or_l={or_low:.6f}",
        f"width={width_pct:.3f}%",
        f"vol={vol:.2f}/ma={vma:.2f}",
        f"vwap={vwap:.6f}",
        f"mode={c.entry_mode}",
    ]

    if c.entry_mode == "retest":
        long_ok = _retest_long(sess_pos, or_high) and _had_volume_breakout_long(
            sess_pos, or_high, period=c.vol_ma_period, mult=c.vol_mult
        )
        short_ok = _retest_short(sess_pos, or_low) and _had_volume_breakout_short(
            sess_pos, or_low, period=c.vol_ma_period, mult=c.vol_mult
        )
    else:
        if not vol_ok:
            return flat("volume_filter")
        long_ok = _breakout_long(closes, or_high=or_high, confirm_bars=c.confirm_bars, no_soften=c.confirm_no_soften)
        short_ok = _breakout_short(closes, or_low=or_low, confirm_bars=c.confirm_bars, no_soften=c.confirm_no_soften)

    if long_ok and short_ok:
        return OrbSignal(sym, entry_px, "FLAT", "ORB_NO_TRADE", "low", reasons + ["ambiguous"])
    if not long_ok and not short_ok:
        return OrbSignal(sym, entry_px, "FLAT", "ORB_NO_TRADE", "low", reasons + ["no_breakout"])

    side = "LONG" if long_ok else "SHORT"
    if c.vwap_filter:
        vwap_ref = float(sess_pos["close"].iloc[-1])
        if side == "LONG" and vwap_ref <= vwap:
            return flat("below_vwap")
        if side == "SHORT" and vwap_ref >= vwap:
            return flat("above_vwap")

    entry_px = entry_price_for_side(
        side=side,
        or_high=or_high,
        or_low=or_low,
        tick_size=c.tick_size,
        tick_offset=c.entry_tick_offset,
    )
    play = ("ORB_RETEST_" if c.entry_mode == "retest" else "ORB_BREAKOUT_") + side
    sl, tp, r_unit = compute_sl_tp(
        side=side, entry=entry_px, or_high=or_high, or_low=or_low, cfg=c, daily_atr=daily_atr
    )
    if sl is None:
        reason = "atr_unavailable" if (c.sl_mode or "").strip().lower() == "atr_pct" else "sl_tp_failed"
        return OrbSignal(sym, entry_px, "FLAT", "ORB_NO_TRADE", "low", reasons + [reason])
    if (c.exit_mode or "").strip().lower() != "eod" and tp is None:
        return OrbSignal(sym, entry_px, "FLAT", "ORB_NO_TRADE", "low", reasons + ["sl_tp_failed"])

    notion = compute_position_notional(entry=entry_px, sl=sl, cfg=c, bot_equity_usdt=bot_equity_usdt)
    atr_note = f"atr={daily_atr:.6f}" if daily_atr else ""
    if atr_note:
        reasons.append(atr_note)

    return OrbSignal(
        symbol=sym,
        price=round(entry_px, 8),
        side=side,
        play=play,
        confidence="high",
        reasons=reasons + [f"signal_{side.lower()}"],
        or_high=round(or_high, 8),
        or_low=round(or_low, 8),
        or_mid=round(float(pack["or_mid"]), 8),
        or_width_pct=round(width_pct, 4),
        session_date=session_date,
        entry_bar_open_ms=int(asof_open_ms),
        sl_price=sl,
        tp_price=tp,
        r_unit=r_unit,
        paper_notional_usdt=round(notion, 4),
        volume=round(vol, 4),
        vol_ma=round(vma, 4),
    )
