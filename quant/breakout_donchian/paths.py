"""Donchian breakout lane paths."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def resolve_breakout_donchian_symbols_path() -> Path:
    return PROJECT_ROOT / "config" / "breakout_donchian" / "symbols.txt"
