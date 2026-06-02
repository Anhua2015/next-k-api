"""Moss2 lane env configuration."""

from __future__ import annotations

import os


def env_truthy(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name, "")
    if not str(raw).strip():
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


MOSS2_QUANT_ENABLED = env_truthy("MOSS2_QUANT_ENABLED", default=False)
MOSS2_QUANT_SCHEDULER_ENABLED = env_truthy("MOSS2_QUANT_SCHEDULER_ENABLED", default=False)

MOSS2_QUANT_ROBOT_TARGET = max(
    1, int(os.getenv("MOSS2_QUANT_ROBOT_TARGET", "6") or 6)
)
MOSS2_QUANT_SYMBOL_POOL_TARGET = max(
    1, int(os.getenv("MOSS2_QUANT_SYMBOL_POOL_TARGET", "30") or 30)
)
MOSS2_QUANT_SWITCH_COOLDOWN_MINUTES = max(
    1, int(os.getenv("MOSS2_QUANT_SWITCH_COOLDOWN_MINUTES", "45") or 45)
)
MOSS2_QUANT_SCAN_INTERVAL_MINUTES = max(
    1, int(os.getenv("MOSS2_QUANT_SCAN_INTERVAL_MINUTES", "15") or 15)
)
MOSS2_QUANT_ALLOW_DAILY_OPTIMIZE_SYNC = env_truthy(
    "MOSS2_QUANT_ALLOW_DAILY_OPTIMIZE_SYNC",
    default=False,
)


def scheduler_enabled() -> bool:
    return bool(MOSS2_QUANT_ENABLED and MOSS2_QUANT_SCHEDULER_ENABLED)

