"""1m FVG 扫描与近沿限价成交（5m 突破确认后）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

import pandas as pd

from orb.core.config import OrbConfig
from orb.core.session import session_anchor_ms


@dataclass(frozen=True)
class FvgZone:
    side: str
    low: float
    high: float
    form_bar_open_ms: int
    """第三根 1m K 的 open_time（FVG 形成完成时刻）。"""


def fvg_min_gap_pct(cfg: OrbConfig) -> float:
    raw = __import__("os").getenv("ORB_FVG_MIN_GAP_PCT", "0.01")
    try:
        return max(0.0, float(str(raw).strip()))
    except ValueError:
        return 0.01


def prox_entry_for_zone(zone: FvgZone) -> float:
    """近沿限价：LONG 挂缺口上沿（回落先触）；SHORT 挂缺口下沿（反弹先触）。"""
    side_u = str(zone.side).upper()
    if side_u == "LONG":
        return float(zone.high)
    if side_u == "SHORT":
        return float(zone.low)
    return float(zone.low)


def quote_fvg_limit_sig(
    sig: Any,
    zone: FvgZone,
    *,
    cfg: OrbConfig,
    daily_atr: Optional[float] = None,
) -> Optional[Any]:
    """FVG 近沿限价挂单报价（实盘 LIMIT / 纸面 pending）。"""
    from dataclasses import replace

    from orb.core.signals import compute_sl_tp

    entry_px = prox_entry_for_zone(zone)
    side_u = str(sig.side).upper()
    sl, tp, risk = compute_sl_tp(
        side=side_u,
        entry=float(entry_px),
        or_high=float(sig.or_high),
        or_low=float(sig.or_low),
        cfg=cfg,
        daily_atr=daily_atr,
    )
    if sl is None:
        sl = stop_loss_for_fvg_fill(
            side=side_u,
            fill_px=float(entry_px),
            sig=sig,
            cfg=cfg,
            daily_atr=daily_atr,
        )
    if sl is None:
        return None
    if risk is None or float(risk) <= 0:
        risk = round(abs(float(entry_px) - float(sl)), 8)
    return replace(
        sig,
        price=round(float(entry_px), 8),
        sl_price=float(sl),
        tp_price=float(tp) if tp is not None else None,
        r_unit=float(risk),
    )


def stop_loss_for_fvg_fill(
    *,
    side: str,
    fill_px: float,
    sig: Any,
    cfg: OrbConfig,
    daily_atr: Optional[float] = None,
) -> Optional[float]:
    """止损从近沿成交价重算（entry=近沿挂单价），不用 OR 突破价平移。"""
    from orb.core.signals import compute_sl_tp

    if fill_px <= 0:
        return None
    side_u = str(side).upper()
    sl, _, _ = compute_sl_tp(
        side=side_u,
        entry=float(fill_px),
        or_high=float(sig.or_high),
        or_low=float(sig.or_low),
        cfg=cfg,
        daily_atr=daily_atr,
    )
    if sl is not None:
        return float(sl)
    r_unit = getattr(sig, "r_unit", None)
    if r_unit is not None and float(r_unit) > 0:
        r = float(r_unit)
        return round(fill_px - r, 8) if side_u == "LONG" else round(fill_px + r, 8)
    orig_sl = float(sig.sl_price or 0)
    orig_entry = float(sig.price or 0)
    if orig_sl <= 0 or orig_entry <= 0:
        return None
    stop_dist = abs(orig_entry - orig_sl)
    if stop_dist <= 0:
        return None
    return round(fill_px - stop_dist, 8) if side_u == "LONG" else round(fill_px + stop_dist, 8)


def _gap_ok(gap: float, ref: float, min_gap_pct: float) -> bool:
    if gap <= 0 or ref <= 0:
        return False
    return gap / ref * 100.0 >= min_gap_pct


def scan_first_fvg(
    df1: pd.DataFrame,
    *,
    side: str,
    after_ms: int,
    before_ms: int,
    or_end_ms: int,
    min_gap_pct: float,
) -> Optional[FvgZone]:
    """在 after_ms 之后找第一个有效 1m FVG（三 K 缺口）。"""
    if df1 is None or df1.empty:
        return None
    side_u = str(side).upper()
    sub = df1[(df1["open_time"] > int(after_ms)) & (df1["open_time"] <= int(before_ms))].sort_values(
        "open_time"
    )
    if len(sub) < 3:
        return None
    rows = sub.reset_index(drop=True)
    for i in range(2, len(rows)):
        c0 = rows.iloc[i - 2]
        c2 = rows.iloc[i]
        if int(c0["open_time"]) < int(or_end_ms):
            continue
        ref = float(c2["close"]) if float(c2["close"]) > 0 else float(c0["close"])
        if side_u == "SHORT":
            gap = float(c0["low"]) - float(c2["high"])
            if gap > 0 and _gap_ok(gap, ref, min_gap_pct):
                return FvgZone(
                    side="SHORT",
                    low=round(float(c2["high"]), 8),
                    high=round(float(c0["low"]), 8),
                    form_bar_open_ms=int(c2["open_time"]),
                )
        elif side_u == "LONG":
            gap = float(c2["low"]) - float(c0["high"])
            if gap > 0 and _gap_ok(gap, ref, min_gap_pct):
                return FvgZone(
                    side="LONG",
                    low=round(float(c0["high"]), 8),
                    high=round(float(c2["low"]), 8),
                    form_bar_open_ms=int(c2["open_time"]),
                )
    return None


def first_or_reclaim_bar_ms(
    df5: pd.DataFrame,
    *,
    after_ms: int,
    before_ms: int,
    or_high: float,
    or_low: float,
) -> Optional[int]:
    """第一个 5m 收盘回到 OR 内的 bar open_time。"""
    if df5 is None or df5.empty:
        return None
    sub = df5[(df5["open_time"] > int(after_ms)) & (df5["open_time"] <= int(before_ms))].sort_values(
        "open_time"
    )
    for _, row in sub.iterrows():
        close = float(row["close"])
        if or_low <= close <= or_high:
            return int(row["open_time"])
    return None


def find_limit_fill(
    df1: pd.DataFrame,
    *,
    side: str,
    entry_px: float,
    after_ms: int,
    before_ms: int,
) -> Optional[Tuple[int, float]]:
    """近沿限价：SHORT 挂高卖（high>=px）；LONG 挂低买（low<=px）。"""
    if df1 is None or df1.empty or entry_px <= 0:
        return None
    side_u = str(side).upper()
    sub = df1[(df1["open_time"] > int(after_ms)) & (df1["open_time"] <= int(before_ms))].sort_values(
        "open_time"
    )
    for _, row in sub.iterrows():
        h, l = float(row["high"]), float(row["low"])
        bo = int(row["open_time"])
        if side_u == "SHORT" and h >= entry_px:
            return bo, float(entry_px)
        if side_u == "LONG" and l <= entry_px:
            return bo, float(entry_px)
    return None


def or_end_ms_for_session(*, anchor_ms: int, cfg: OrbConfig) -> int:
    return int(anchor_ms) + max(1, int(cfg.or_minutes)) * 60_000


def find_fvg_limit_entry(
    sig: Any,
    df1: pd.DataFrame,
    df5: pd.DataFrame,
    *,
    scan_ms: int,
    close_ms: int,
    bar: int,
    cfg: OrbConfig,
    asof_ms: Optional[int] = None,
    daily_atr: Optional[float] = None,
) -> Tuple[Optional[Any], str, Optional[FvgZone]]:
    """5m 确认后：找 FVG 近沿限价是否成交。asof_ms=实盘当前时刻（不偷看未来）。"""
    from dataclasses import replace

    from orb.core.or_entry_fill import order_deadline_for_signal

    entry_bo = int(sig.entry_bar_open_ms or 0)
    if entry_bo <= 0:
        return None, "no_entry_bar", None

    anchor = session_anchor_ms(int(scan_ms), tz=cfg.session_tz, session_open_time=cfg.session_open_time)
    or_end_ms = or_end_ms_for_session(anchor_ms=anchor, cfg=cfg)
    confirm_close_ms = int(entry_bo) + int(bar)
    deadline = order_deadline_for_signal(scan_ms=scan_ms, cfg=cfg, session_close_ms=int(close_ms))
    search_end = min(int(deadline), int(asof_ms)) if asof_ms is not None else int(deadline)
    min_gap = fvg_min_gap_pct(cfg)
    side = str(sig.side).upper()
    or_high = float(sig.or_high)
    or_low = float(sig.or_low)

    cursor = int(confirm_close_ms)
    last_reclaim_ms = -1
    while cursor < search_end:
        reclaim = first_or_reclaim_bar_ms(
            df5,
            after_ms=confirm_close_ms,
            before_ms=cursor,
            or_high=or_high,
            or_low=or_low,
        )
        # 同一根 OR reclaim 只跳过一次；否则 cursor 会被反复打回死循环（6/12 等震荡日卡死）
        if reclaim is not None and int(reclaim) > last_reclaim_ms:
            last_reclaim_ms = int(reclaim)
            cursor = int(reclaim) + int(bar)
            continue

        zone = scan_first_fvg(
            df1,
            side=side,
            after_ms=cursor,
            before_ms=search_end,
            or_end_ms=or_end_ms,
            min_gap_pct=min_gap,
        )
        if zone is None:
            if asof_ms is not None and search_end < int(deadline):
                return None, "fvg_pending", None
            return None, "fvg_not_found", None

        entry_px = prox_entry_for_zone(zone)
        fill_after = int(zone.form_bar_open_ms) + 60_000
        hit = find_limit_fill(
            df1,
            side=side,
            entry_px=entry_px,
            after_ms=fill_after,
            before_ms=search_end,
        )
        if hit is not None:
            fill_ms, fill_px = hit
            quote = quote_fvg_limit_sig(sig, zone, cfg=cfg, daily_atr=daily_atr)
            if quote is None:
                new_sl = stop_loss_for_fvg_fill(
                    side=side,
                    fill_px=float(fill_px),
                    sig=sig,
                    cfg=cfg,
                    daily_atr=daily_atr,
                )
                if new_sl is None:
                    return None, "fvg_sl_invalid", zone
                risk = round(abs(float(fill_px) - float(new_sl)), 8)
                fill_sig = replace(
                    sig,
                    price=round(fill_px, 8),
                    sl_price=new_sl,
                    entry_bar_open_ms=int(fill_ms),
                    fvg_confirm_bar_ms=int(sig.entry_bar_open_ms or 0) or None,
                    r_unit=risk,
                )
            else:
                fill_sig = replace(
                    quote,
                    price=round(float(fill_px), 8),
                    entry_bar_open_ms=int(fill_ms),
                    fvg_confirm_bar_ms=int(sig.entry_bar_open_ms or 0) or None,
                )
            return fill_sig, "ok", zone

        zone_exhausted = search_end > int(zone.form_bar_open_ms) + int(bar)
        if asof_ms is not None and search_end < int(deadline) and not zone_exhausted:
            quote = quote_fvg_limit_sig(sig, zone, cfg=cfg, daily_atr=daily_atr)
            if quote is not None:
                return quote, "fvg_limit_pending", zone
            cursor = int(zone.form_bar_open_ms) + 60_000
            continue

        cursor = int(zone.form_bar_open_ms) + 60_000

    if asof_ms is not None and search_end < int(deadline):
        return None, "fvg_pending", None
    return None, "fvg_limit_not_filled", None


def synthesize_fvg_fill_from_protocol(
    sig: Any,
    cfg: OrbConfig,
    *,
    now_ms: int,
    quote: Optional[Any] = None,
    daily_atr: Optional[float] = None,
    protocol_entry_px: Optional[float] = None,
) -> Optional[Any]:
    """Protocol LIMIT 已成交但 1m 尚未检测到 fill 时，合成纸面 fill。"""
    from dataclasses import replace

    entry_px = protocol_entry_px
    if entry_px is None and quote is not None:
        entry_px = float(getattr(quote, "price", 0) or 0)
    if entry_px is None or float(entry_px) <= 0:
        entry_px = float(getattr(sig, "price", 0) or 0)
    if entry_px <= 0:
        return None
    confirm = int(getattr(sig, "fvg_confirm_bar_ms", None) or sig.entry_bar_open_ms or 0)
    sl = None
    tp = None
    if quote is not None:
        sl = getattr(quote, "sl_price", None)
        tp = getattr(quote, "tp_price", None)
    if sl is None:
        sl = stop_loss_for_fvg_fill(
            side=str(sig.side),
            fill_px=float(entry_px),
            sig=sig,
            cfg=cfg,
            daily_atr=daily_atr,
        )
    if sl is None:
        return None
    risk = round(abs(float(entry_px) - float(sl)), 8)
    return replace(
        sig,
        price=round(float(entry_px), 8),
        sl_price=float(sl),
        tp_price=float(tp) if tp is not None else getattr(sig, "tp_price", None),
        entry_bar_open_ms=int(now_ms),
        fvg_confirm_bar_ms=confirm or None,
        r_unit=risk,
    )


def uses_fvg_entry(cfg: OrbConfig) -> bool:
    return (cfg.entry_fill or "").strip().lower() in ("fvg_prox", "fvg")


def resolve_fvg_prox_fill(
    *,
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
    scans: Optional[List[int]],
    daily_atr: Optional[float] = None,
) -> Tuple[Optional[dict], str]:
    """Gate 通过后：等 1m FVG 形成 → 近沿限价 → 成交或拒单。"""
    from orb.ml.live_gate_sim import _resolve_trade_row

    fill_sig, reason, zone = find_fvg_limit_entry(
        sig,
        df1,
        df5,
        scan_ms=scan_ms,
        close_ms=close_ms,
        bar=bar,
        cfg=cfg,
        asof_ms=None,
        daily_atr=daily_atr,
    )
    if fill_sig is None:
        return None, reason

    notion = notional
    if cfg.uses_risk_sizing() and float(cfg.fixed_notional_usdt or 0) <= 0:
        from orb.core.signals import compute_position_notional

        notion = compute_position_notional(
            entry=float(fill_sig.price),
            sl=float(fill_sig.sl_price),
            cfg=cfg,
            bot_equity_usdt=wallet_before,
        )
    row = _resolve_trade_row(
        sym=sym,
        sig=fill_sig,
        session_date=session_date,
        scan_ms=scan_ms,
        entry_bo=int(fill_sig.entry_bar_open_ms or 0),
        df1=df1,
        close_ms=close_ms,
        bar=bar,
        cfg=cfg,
        notional=notion,
        wallet_before=wallet_before,
        robot_id=robot_id,
        scans=scans,
    )
    if not row:
        return None, "no_trade_row"
    row["entry_mode"] = "fvg_prox"
    row["signal_entry"] = float(sig.price)
    if zone is not None:
        row["fvg_low"] = zone.low
        row["fvg_high"] = zone.high
        row["fvg_form_ms"] = zone.form_bar_open_ms
    row["fill_bar_open_ms"] = int(fill_sig.entry_bar_open_ms or 0)
    row["chase_slip"] = round(float(fill_sig.price) - float(sig.price), 6)
    return row, "ok"
