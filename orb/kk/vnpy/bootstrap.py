"""将本地 vnpy-master 与 vnpy_ctastrategy 加入 import 路径。"""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_vnpy_path() -> Path:
    api_root = Path(__file__).resolve().parents[3]
    vnpy_root = api_root / "vnpy-master"
    if vnpy_root.is_dir():
        root_s = str(vnpy_root)
        if root_s not in sys.path:
            sys.path.insert(0, root_s)
    return vnpy_root


def import_vnpy_cta():
    ensure_vnpy_path()
    from vnpy_ctastrategy import CtaStrategyApp, CtaTemplate  # noqa: F401
    from vnpy_ctastrategy.strategies.king_keltner_strategy import KingKeltnerStrategy  # noqa: F401

    return CtaStrategyApp, KingKeltnerStrategy
