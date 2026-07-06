"""vnpy CTA 回测定仓（与 KK 纸面引擎口径一致）。"""

from __future__ import annotations

import math


def fixed_size_for_equity(
    price: float,
    *,
    equity_usdt: float,
    risk_pct: float = 0.01,
    trail_frac: float = 0.008,
    safety_pct: float = 0.0,
    max_notional_usdt: float = 0.0,
) -> float:
    px = max(1e-9, float(price or 0.0))
    equity = float(equity_usdt or 1000.0)
    risk = float(risk_pct or 0.01)
    trail = max(1e-9, float(trail_frac or 0.008))
    safety = float(safety_pct or 0.0)
    notion = equity * risk * (1.0 - safety) / trail
    cap = float(max_notional_usdt or 0.0)
    if cap > 0:
        notion = min(notion, cap)
    vol = notion / px
    return _round_order_volume(vol, px)


def _round_order_volume(volume: float, price: float) -> float:
    px = max(1e-9, float(price or 1.0))
    raw = max(0.001, float(volume))
    if px >= 1000:
        step = 0.001
    elif px >= 100:
        step = 0.01
    elif px >= 10:
        step = 0.1
    else:
        step = 1.0
    return max(step, math.floor(raw / step) * step)
