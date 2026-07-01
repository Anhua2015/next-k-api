"""CTA 撮合：滑点与 stop 成交价（贴近实盘）。"""

from __future__ import annotations


def slip_frac(bps: float) -> float:
    return max(0.0, float(bps or 0.0)) / 10000.0


def entry_fill_px(side: int, trigger_px: float, slip_bps: float) -> float:
    """Stop 入场：多头买贵、空头卖便宜。"""
    s = slip_frac(slip_bps)
    px = float(trigger_px)
    if int(side) == 1:
        return px * (1.0 + s)
    return px * (1.0 - s)


def stop_exit_fill_px(
    side: int,
    stop_px: float,
    *,
    bar_open: float,
    slip_bps: float,
) -> float:
    """Stop 出场：跳空时用 open，再叠加不利滑点。"""
    s = slip_frac(slip_bps)
    stop = float(stop_px)
    o = float(bar_open)
    if int(side) == 1:
        raw = o if o < stop else stop
        return raw * (1.0 - s)
    raw = o if o > stop else stop
    return raw * (1.0 + s)


def market_exit_fill_px(side: int, ref_px: float, slip_bps: float) -> float:
    """EOD / 市价平仓。"""
    s = slip_frac(slip_bps)
    px = float(ref_px)
    if int(side) == 1:
        return px * (1.0 - s)
    return px * (1.0 + s)
