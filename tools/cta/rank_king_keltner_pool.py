#!/usr/bin/env python3
"""king_keltner 全池排名 — 默认 vnpy；--legacy 用已废弃 1m 触价引擎。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    if "--legacy" in sys.argv:
        sys.argv = [a for a in sys.argv if a != "--legacy"]
        from tools.cta.rank_king_keltner_pool_legacy import main

        main()
    else:
        from tools.cta.rank_kk_vnpy import main

        main()
