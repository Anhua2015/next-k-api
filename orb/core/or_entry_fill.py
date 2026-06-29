"""OR 入场成交模拟：Stop-Limit / 市价追单。"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from orb.core.config import OrbConfig
from orb.core.session import session_anchor_ms
from orb.core.signals import compute_position_notional, limit_price_for_side


def _preplace_leg_fill_px(*, side: str, stop_px: float, limit_px: float, open_px: float) -> float:
    side_u = str(side).upper()
    if side_u == "LONG":
        if open_px >= stop_px:
            return min(float(open_px), float(limit_px))
        return float(stop_px)
    if open_px <= stop_px:
        return max(float(open_px), float(limit_px))
    return float(stop_px)


def _bar_fills_preplace_leg(*, side: str, stop_px: float, limit_px: float, high: float, low: float, open_px: float) -> bool:
    side_u = str(side).upper()
    if side_u == "LONG":
        if high < stop_px:
            return False
        if open_px >= stop_px:
            return open_px <= limit_px
        return low <= limit_px
    if low > stop_px:
        return False
    if open_px <= stop_px:
        return open_px >= limit_px
    return high >= limit_px


def find_preplace_oco_fill(
    df1: pd.DataFrame,
    *,
    long_stop: float,
    short_stop: float,
    after_ms: int,
    before_ms: int,
    cfg: OrbConfig,
    skip_sides: frozenset[str] | None = None,
) -> Optional[tuple[str, int, float]]:
    """OCO preplace：返回先触发的 (side, fill_ms, fill_px)。skip_sides 跳过已用方向。"""
    if df1 is None or df1.empty or long_stop <= 0 or short_stop <= 0:
        return None
    skip = {str(x).upper() for x in (skip_sides or frozenset())}
    long_ok_side = "LONG" not in skip
    short_ok_side = "SHORT" not in skip
    if not long_ok_side and not short_ok_side:
        return None
    long_limit = limit_price_for_side(entry=long_stop, side="LONG", cfg=cfg)
    short_limit = limit_price_for_side(entry=short_stop, side="SHORT", cfg=cfg)
    sub = df1[(df1["open_time"] > int(after_ms)) & (df1["open_time"] <= int(before_ms))]
    for _, row in sub.sort_values("open_time").iterrows():
        h, l, o = float(row["high"]), float(row["low"]), float(row["open"])
        t_ms = int(row["open_time"])
        long_ok = (
            long_ok_side
            and _bar_fills_preplace_leg(
                side="LONG", stop_px=long_stop, limit_px=long_limit, high=h, low=l, open_px=o
            )
        )
        short_ok = (
            short_ok_side
            and _bar_fills_preplace_leg(
                side="SHORT", stop_px=short_stop, limit_px=short_limit, high=h, low=l, open_px=o
            )
        )
        if long_ok and short_ok:
            long_ok = abs(o - long_stop) <= abs(o - short_stop)
            short_ok = not long_ok
        if long_ok:
            px = _preplace_leg_fill_px(side="LONG", stop_px=long_stop, limit_px=long_limit, open_px=o)
            return "LONG", t_ms, round(px, 8)
        if short_ok:
            px = _preplace_leg_fill_px(side="SHORT", stop_px=short_stop, limit_px=short_limit, open_px=o)
            return "SHORT", t_ms, round(px, 8)
    return None


def or_order_deadline_ms(*, or_end_ms: int, cfg: OrbConfig, session_close_ms: int) -> int:
    deadline = int(session_close_ms)
    if int(cfg.trade_window_minutes or 0) > 0:
        deadline = min(deadline, int(or_end_ms) + int(cfg.trade_window_minutes) * 60_000)
    return deadline


def bar_fills_stop_limit(*, side: str, entry_px: float, high: float, low: float) -> bool:
    side_u = str(side).upper()
    if side_u == "LONG":
        return high >= entry_px and low <= entry_px
    if side_u == "SHORT":
        return low <= entry_px and high >= entry_px
    return False


def find_or_stop_limit_fill(
    df1: pd.DataFrame,
    *,
    side: str,
    entry_px: float,
    after_ms: int,
    before_ms: int,
    gap_ok: bool = False,
    honest_fill: bool = False,
) -> Optional[tuple[int, float]]:
    """返回 (fill_bar_open_ms, fill_price)。"""
    if df1 is None or df1.empty or entry_px <= 0:
        return None
    side_u = str(side).upper()
    sub = df1[(df1["open_time"] > int(after_ms)) & (df1["open_time"] <= int(before_ms))]
    for _, row in sub.sort_values("open_time").iterrows():
        h, l, o = float(row["high"]), float(row["low"]), float(row["open"])
        if bar_fills_stop_limit(side=side_u, entry_px=entry_px, high=h, low=l):
            return int(row["open_time"]), float(entry_px)
        if gap_ok:
            if side_u == "LONG" and o >= entry_px:
                px = float(o) if honest_fill else float(entry_px)
                return int(row["open_time"]), px
            if side_u == "SHORT" and o <= entry_px:
                px = float(o) if honest_fill else float(entry_px)
                return int(row["open_time"]), px
    return None


def order_deadline_for_signal(
    *,
    scan_ms: int,
    cfg: OrbConfig,
    session_close_ms: int,
) -> int:
    from orb.core.vol_breakout import is_vol_breakout_mode, vol_breakout_arm_ms

    anchor = session_anchor_ms(int(scan_ms), tz=cfg.session_tz, session_open_time=cfg.session_open_time)
    if is_vol_breakout_mode(cfg):
        or_end_ms = vol_breakout_arm_ms(anchor_ms=int(anchor), cfg=cfg)
    else:
        or_end_ms = anchor + max(1, int(cfg.or_minutes)) * 60_000
    return or_order_deadline_ms(or_end_ms=or_end_ms, cfg=cfg, session_close_ms=session_close_ms)


def resolve_entry_fill(
    *,
    mode: str,
    sym: str,
    sig: Any,
    session_date: str,
    scan_ms: int,
    df1: pd.DataFrame,
    df5: pd.DataFrame,
    close_ms: int,
    bar: int,
    cfg: OrbConfig,
    notional: float,
    wallet_before: float,
    robot_id: int,
    scans: Optional[list],
    daily_atr: Optional[float] = None,
    skip_sides: frozenset[str] | None = None,
) -> tuple[Optional[Dict[str, Any]], str]:
    """返回 (trade_row, reason)。"""
    from dataclasses import replace

    from orb.ml.live_gate_sim import _resolve_trade_row

    mode_l = (mode or "signal").strip().lower()
    entry_bo = int(sig.entry_bar_open_ms or 0)
    signal_entry = float(sig.price)

    def _row_with_fill(sig_for_row: Any, fill_bo: int, fill_px: float, mode_name: str) -> tuple[Optional[Dict[str, Any]], str]:
        row = _resolve_trade_row(
            sym=sym,
            sig=sig_for_row,
            session_date=session_date,
            scan_ms=scan_ms,
            entry_bo=int(fill_bo),
            df1=df1,
            close_ms=close_ms,
            bar=bar,
            cfg=cfg,
            notional=notional,
            wallet_before=wallet_before,
            robot_id=robot_id,
            scans=scans,
        )
        if not row:
            return None, "no_trade_row"
        row["fill_bar_open_ms"] = int(fill_bo)
        row["entry_mode"] = mode_name
        row["signal_entry"] = signal_entry
        row["entry"] = float(fill_px)
        row["chase_slip"] = round(float(fill_px) - signal_entry, 6)
        return row, "ok"

    if mode_l == "signal":
        if entry_bo <= 0:
            return None, "no_entry_bar"
        row = _resolve_trade_row(
            sym=sym,
            sig=sig,
            session_date=session_date,
            scan_ms=scan_ms,
            entry_bo=entry_bo,
            df1=df1,
            close_ms=close_ms,
            bar=bar,
            cfg=cfg,
            notional=notional,
            wallet_before=wallet_before,
            robot_id=robot_id,
            scans=scans,
        )
        if row:
            row["entry_mode"] = "signal"
            row["signal_entry"] = signal_entry
        return (row, "ok") if row else (None, "no_trade_row")

    if mode_l in ("stoplimit", "stoplimit_gap", "stoplimit_honest", "stoplimit_gap_honest"):
        order_ms = int(scan_ms) + int(bar)
        deadline = order_deadline_for_signal(scan_ms=scan_ms, cfg=cfg, session_close_ms=int(close_ms))
        gap = mode_l in ("stoplimit_gap", "stoplimit_gap_honest")
        honest = mode_l in ("stoplimit_honest", "stoplimit_gap_honest")
        hit = find_or_stop_limit_fill(
            df1,
            side=str(sig.side),
            entry_px=signal_entry,
            after_ms=order_ms,
            before_ms=deadline,
            gap_ok=gap,
            honest_fill=honest,
        )
        if hit is None:
            return None, "or_limit_not_filled"
        fill_ms, fill_px = hit
        if honest or gap:
            fill_sig = replace(sig, price=round(fill_px, 8))
            return _row_with_fill(fill_sig, fill_ms, fill_px, mode_l)
        row, reason = _row_with_fill(sig, fill_ms, signal_entry, mode_l)
        return (row, reason) if row else (None, reason)

    if mode_l == "market":
        if entry_bo <= 0 or df5 is None or df5.empty:
            return None, "no_entry_bar"
        hit = df5[df5["open_time"] == entry_bo]
        if hit.empty:
            return None, "bar_not_found"
        chase_px = round(float(hit.iloc[-1]["close"]), 8)
        chase_sig = replace(sig, price=chase_px)
        row, reason = _row_with_fill(chase_sig, entry_bo, chase_px, "market_chase")
        return (row, reason) if row else (None, reason)

    if mode_l == "preplace_stop":
        bundle = getattr(sig, "preplace_arm", None)
        if bundle is None:
            return None, "no_preplace_bundle"
        long_stop = float(bundle.long_sig.price)
        short_stop = float(bundle.short_sig.price)
        arm_ms = int(bundle.long_sig.entry_bar_open_ms or bundle.or_end_ms or 0)
        if arm_ms <= 0:
            return None, "no_arm_ms"
        deadline = order_deadline_for_signal(scan_ms=scan_ms, cfg=cfg, session_close_ms=int(close_ms))
        hit = find_preplace_oco_fill(
            df1,
            long_stop=long_stop,
            short_stop=short_stop,
            after_ms=arm_ms,
            before_ms=deadline,
            cfg=cfg,
            skip_sides=skip_sides,
        )
        if hit is None:
            return None, "preplace_not_filled"
        side, fill_ms, fill_px = hit
        leg_sig = bundle.long_sig if side == "LONG" else bundle.short_sig
        from orb.core.signals import refresh_preplace_leg_after_fill, worst_fill_for_preplace

        fill_leg = refresh_preplace_leg_after_fill(
            leg_sig,
            fill_px=float(fill_px),
            cfg=cfg,
            daily_atr=daily_atr,
            bot_equity_usdt=wallet_before,
        )
        sl_px = float(fill_leg.sl_price or leg_sig.sl_price or 0)
        if sl_px <= 0:
            return None, "sl_missing_after_fill"
        worst = worst_fill_for_preplace(stop_entry=float(leg_sig.price), side=side, cfg=cfg)
        leg_notion = compute_position_notional(
            entry=worst,
            sl=sl_px,
            cfg=cfg,
            bot_equity_usdt=wallet_before,
            for_preplace=True,
            or_width_pct=float(leg_sig.or_width_pct or 0),
        )

        def _row_with_leg(sig_for_row: Any, fill_bo: int, fill_px: float) -> tuple[Optional[Dict[str, Any]], str]:
            row = _resolve_trade_row(
                sym=sym,
                sig=sig_for_row,
                session_date=session_date,
                scan_ms=scan_ms,
                entry_bo=int(fill_bo),
                df1=df1,
                close_ms=close_ms,
                bar=bar,
                cfg=cfg,
                notional=leg_notion,
                wallet_before=wallet_before,
                robot_id=robot_id,
                scans=scans,
            )
            if not row:
                return None, "no_trade_row"
            row["fill_bar_open_ms"] = int(fill_bo)
            row["entry_mode"] = "preplace_stop"
            row["signal_entry"] = float(leg_sig.price)
            row["entry"] = float(fill_px)
            row["chase_slip"] = round(float(fill_px) - float(leg_sig.price), 6)
            row["side"] = side
            return row, "ok"

        fill_sig = replace(fill_leg, price=round(fill_px, 8))
        row, reason = _row_with_leg(fill_sig, fill_ms, fill_px)
        return (row, reason) if row else (None, reason)

    return None, f"unknown_mode:{mode_l}"
