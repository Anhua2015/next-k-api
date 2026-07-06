"""Trading ORB 实盘守卫。"""

from __future__ import annotations

import os

from orb.trading_orb.config import OrbVnpyConfig


def binance_credentials_configured() -> bool:
    return bool(
        (os.getenv("BINANCE_API_KEY") or "").strip()
        and (os.getenv("BINANCE_API_SECRET") or "").strip()
    )


def live_enabled(cfg: OrbVnpyConfig) -> bool:
    if not cfg.live_enabled:
        return False
    if not binance_credentials_configured():
        return False
    return True
