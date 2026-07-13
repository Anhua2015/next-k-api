"""Donchian breakout lane switches."""

from __future__ import annotations

from quant.common.strategy_switch import StrategySwitchSpec

BREAKOUT_DONCHIAN_SWITCH = StrategySwitchSpec(
    lane="breakout_donchian",
    title="Donchian Breakout Scanner",
    enabled_keys=(
        "STRATEGY_BREAKOUT_DONCHIAN_ENABLED",
        "BREAKOUT_DONCHIAN_VNPY_ENABLED",
    ),
    live_keys=(
        "STRATEGY_BREAKOUT_DONCHIAN_LIVE",
        "BREAKOUT_DONCHIAN_VNPY_LIVE_ENABLED",
    ),
    shadow_keys=(
        "STRATEGY_BREAKOUT_DONCHIAN_SHADOW",
        "BREAKOUT_DONCHIAN_VNPY_SHADOW",
    ),
    default_enabled=False,
    default_live=False,
    default_shadow=False,
)
