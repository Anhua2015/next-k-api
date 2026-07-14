"""Tier-A mom-turn lane switches (spot)."""

from __future__ import annotations

from quant.common.strategy_switch import StrategySwitchSpec

TIER_A_MOM_SWITCH = StrategySwitchSpec(
    lane="tier_a_mom",
    title="Tier-A Mom Turn (Spot)",
    enabled_keys=(
        "STRATEGY_TIER_A_MOM_ENABLED",
        "TIER_A_MOM_VNPY_ENABLED",
    ),
    live_keys=(
        "STRATEGY_TIER_A_MOM_LIVE",
        "TIER_A_MOM_VNPY_LIVE_ENABLED",
    ),
    shadow_keys=(
        "STRATEGY_TIER_A_MOM_SHADOW",
        "TIER_A_MOM_VNPY_SHADOW",
    ),
    default_enabled=False,
    default_live=False,
    default_shadow=False,
)
