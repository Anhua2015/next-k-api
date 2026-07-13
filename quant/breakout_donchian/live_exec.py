"""Donchian breakout live guard."""

from __future__ import annotations

from quant.breakout_donchian.config import BreakoutDonchianConfig
from quant.common.exchange_env import resolve_live_exchange_id
from quant.engine.exchanges.registry import get_adapter


def live_enabled(cfg: BreakoutDonchianConfig) -> bool:
    if not cfg.live_enabled:
        return False
    return get_adapter(resolve_live_exchange_id(cfg.live_exchange)).credentials_configured()
