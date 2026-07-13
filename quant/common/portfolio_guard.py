"""Cross-lane portfolio guards for combined vnpy engine."""

from __future__ import annotations

import os
from typing import Any, Set

from quant.common.kline_cache import norm_symbol

SHARED_WATCHLIST_LANES = frozenset({"squeeze_breakout", "breakout_donchian"})


def _int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def portfolio_max_total_positions() -> int:
    return max(0, _int_env("PORTFOLIO_MAX_TOTAL_POSITIONS", 5))


def portfolio_max_per_symbol() -> int:
    return max(1, _int_env("PORTFOLIO_MAX_PER_SYMBOL", 1))


def lanes_share_watchlist_pool(name_a: str, name_b: str, cfg_a: Any, cfg_b: Any) -> bool:
    if name_a not in SHARED_WATCHLIST_LANES or name_b not in SHARED_WATCHLIST_LANES:
        return False
    return bool(getattr(cfg_a, "use_scanner_watchlist", False)) and bool(
        getattr(cfg_b, "use_scanner_watchlist", False)
    )


def portfolio_allows_open(sym: str, cfg: Any, *, active_symbols: Set[str]) -> tuple[bool, str]:
    """Global open-order guard across all lanes on one gateway."""
    sym = norm_symbol(sym)
    max_total = portfolio_max_total_positions()
    max_per = portfolio_max_per_symbol()

    if sym in active_symbols and max_per <= 1:
        return False, f"组合风控：{sym} 已有持仓，拒单"

    if max_total > 0 and sym not in active_symbols and len(active_symbols) >= max_total:
        return False, f"组合风控：总持仓已达上限 {max_total}，拒单 {sym}"

    lane_cap = int(getattr(cfg, "max_open_positions", 0) or 0)
    if lane_cap > 0:
        pool = {norm_symbol(s) for s in cfg.symbol_list()}
        lane_open = sum(1 for s in active_symbols if s in pool)
        if sym not in active_symbols and lane_open >= lane_cap:
            return False, f"Lane {getattr(cfg, 'lane', '')} 持仓已达上限 {lane_cap}，拒单 {sym}"

    return True, ""
