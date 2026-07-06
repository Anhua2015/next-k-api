"""Trading ORB 路径。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def resolve_orb_vnpy_symbols_path() -> Path:
    return ROOT / "config" / "trading_orb" / "symbols.txt"
