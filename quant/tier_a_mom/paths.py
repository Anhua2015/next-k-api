"""Tier-A mom-turn paths."""

from __future__ import annotations

from pathlib import Path

from quant.common.paths import PROJECT_ROOT


def resolve_tier_a_mom_symbols_path() -> Path:
    return PROJECT_ROOT / "config" / "tier_a_mom" / "symbols.txt"
