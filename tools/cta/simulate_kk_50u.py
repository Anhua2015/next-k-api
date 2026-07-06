#!/usr/bin/env python3
"""KK 池回测默认入口：vnpy 官方 BacktestingEngine（与实盘同策略类）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    if "--legacy" in sys.argv:
        sys.argv = [a for a in sys.argv if a != "--legacy"]
        from tools.cta.simulate_kk_50u_legacy import main

        main()
    else:
        from tools.cta.simulate_kk_vnpy_50u import main

        main()
