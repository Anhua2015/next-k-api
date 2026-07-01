"""KK vnpy 下单数量与名义换算（与纸面 CTA 对齐）。"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from orb.cta.strategies import KK_TRAILING_PCT

if TYPE_CHECKING:
    from orb.core.config import OrbConfig
    from orb.kk.config import KKConfig


def trailing_risk_frac() -> float:
    return float(KK_TRAILING_PCT) / 100.0


def order_volume_to_notional(
    kk: "KKConfig",
    price: float,
    *,
    volume: float | None = None,
    equity_usdt: float | None = None,
    orb_cfg: "OrbConfig | None" = None,
) -> float:
    px = max(1e-9, float(price or 0.0))
    if volume is not None and float(volume) > 0:
        notion = float(volume) * px
    else:
        cfg = orb_cfg or kk.orb_session_cfg()
        safety = float(getattr(cfg, "position_safety_pct", 0.0) or 0.0)
        risk_frac = float(kk.risk_pct or 0.01)
        trail = trailing_risk_frac()
        equity = float(equity_usdt if equity_usdt is not None else (kk.equity_usdt or 14.0))
        notion = equity * risk_frac * (1.0 - safety) / trail
    cap = float(kk.max_notional_usdt or 0.0)
    if cap > 0:
        notion = min(notion, cap)
    return max(0.0, notion)


def round_order_volume(volume: float, price: float) -> float:
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


def fixed_size_for_symbol(
    kk: "KKConfig",
    symbol: str,
    price: float,
    *,
    equity_usdt: float | None = None,
    orb_cfg: "OrbConfig | None" = None,
) -> float:
    notion = order_volume_to_notional(
        kk,
        price,
        equity_usdt=equity_usdt,
        orb_cfg=orb_cfg,
    )
    vol = notion / max(1e-9, float(price or 0.0))
    return round_order_volume(vol, price)
