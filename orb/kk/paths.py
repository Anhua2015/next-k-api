"""King Keltner 路径（与 ORB 隔离）。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def resolve_kk_symbols_path() -> Path:
    return ROOT / "config" / "kk" / "symbols.txt"


def resolve_symbols_path() -> Path:
    """KK 标的列表（兼容旧 tools 脚本 import 名）。"""
    return resolve_kk_symbols_path()


def resolve_kk_output_dir() -> Path:
    return ROOT / "output" / "kk"
