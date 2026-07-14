"""Tier-A mom-turn sizing — compound equity fraction (spot)."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from quant.tier_a_mom.config import TierAMomConfig


def round_order_volume(volume: float, price: float) -> float:
    px = max(1e-9, float(price or 1.0))
    raw = max(0.001, float(volume))
    if px >= 1000:
        step = 0.001
    elif px >= 100:
        step = 0.01
    else:
        step = 0.1
    return max(step, math.floor(raw / step) * step)


def size_for_tier_a_mom(
    cfg: "TierAMomConfig",
    price: float,
    *,
    equity_usdt: float | None = None,
) -> float:
    """Notional = equity * position_pct (default 15%), spot long."""
    px = max(1e-9, float(price or 0.0))
    eq = float(equity_usdt if equity_usdt is not None else (cfg.equity_usdt or 100.0))
    pct = max(0.0, min(1.0, float(cfg.position_pct or 0.15)))
    notion = eq * pct
    cap = float(cfg.max_notional_usdt or 0.0)
    if cap > 0:
        notion = min(notion, cap)
    notion = min(notion, eq)
    if notion <= 0:
        return 0.0
    return round_order_volume(notion / px, px)
