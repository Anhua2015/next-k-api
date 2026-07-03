"""启动时从币安同步持仓到 vnpy 策略 pos。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from orb.core.kline_cache import norm_symbol
from orb.kk.vnpy.binance_account import fetch_position_amounts

logger = logging.getLogger(__name__)


def sync_cta_positions(cta_engine: Any, symbols: List[str]) -> Dict[str, float]:
    """将交易所净持仓写入各 KK 策略 pos；无仓则 cancel_all。"""
    amounts = fetch_position_amounts(symbols)
    out: Dict[str, float] = {}
    for raw in symbols:
        sym = norm_symbol(raw)
        name = f"kk_{sym.lower()}"
        strat = cta_engine.strategies.get(name)
        if strat is None:
            continue
        amt = float(amounts.get(sym, 0.0) or 0.0)
        out[sym] = amt
        old = float(getattr(strat, "pos", 0) or 0.0)
        if abs(old - amt) > 1e-9:
            logger.warning("[kk-vnpy] sync pos %s: strategy=%s exchange=%s", sym, old, amt)
        strat.pos = amt
        if amt == 0.0:
            try:
                strat.cancel_all()
            except Exception as exc:
                logger.warning("[kk-vnpy] cancel_all after sync %s: %s", sym, exc)
        if cta_engine:
            try:
                cta_engine.sync_strategy_data(strat)
            except Exception:
                pass
    return out
