"""vnpy 活跃 lane：Trading ORB 优先于 KK。"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from orb.kk.config import KKConfig
from orb.trading_orb.config import OrbVnpyConfig


def get_active_vnpy_config() -> Tuple[Optional[str], Any]:
    orb = OrbVnpyConfig.from_env()
    if orb.enabled and orb.is_vnpy_engine():
        return "trading_orb", orb
    kk = KKConfig.from_env()
    if kk.enabled and kk.is_vnpy_engine():
        return "king_keltner", kk
    return None, None


def active_lane_session_cfg():
    lane, cfg = get_active_vnpy_config()
    if cfg is None:
        return OrbVnpyConfig.from_env().orb_session_cfg()
    return cfg.orb_session_cfg()


def lane_rth_only() -> bool:
    _, cfg = get_active_vnpy_config()
    if cfg is None:
        return True
    return bool(getattr(cfg, "rth_only", True))


def lane_vnpy_idle_outside_rth() -> bool:
    _, cfg = get_active_vnpy_config()
    if cfg is None:
        return True
    return bool(getattr(cfg, "vnpy_idle_outside_rth", True))


def lane_eod_flat_and_enabled(engine) -> bool:
    lane, cfg = get_active_vnpy_config()
    if cfg is None:
        return False
    if not getattr(cfg, "enabled", False) or not getattr(cfg, "eod_flat", False):
        return False
    for strategy in getattr(engine, "strategies", {}).values():
        if getattr(strategy, "pos", 0) != 0:
            return True
    return False
