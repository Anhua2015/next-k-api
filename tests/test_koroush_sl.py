#!/usr/bin/env python3
"""Koroush 止损扩窗熔断测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

_API_ROOT = Path(__file__).resolve().parent.parent
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from zct_strategy_config import StrategyConfig
from zct_vwap_signal_scanner import _widen_sl_min_risk_long


class KoroushSlTests(unittest.TestCase):
    def _make_sdf(self, lows: list[float]) -> pd.DataFrame:
        n = len(lows)
        return pd.DataFrame(
            {
                "low": lows,
                "high": [x * 1.01 for x in lows],
                "close": lows,
                "open": lows,
            }
        )

    def test_widen_within_cap_returns_sl(self) -> None:
        cfg = StrategyConfig(
            min_sl_pct=0.01,
            koroush_min_stop_distance_pct=0.01,
            max_sl_widen_pct=0.05,
            swing_lookback=5,
        )
        sdf = self._make_sdf([98.5] * 30)
        entry = 100.0
        sl_init = 99.0
        out = _widen_sl_min_risk_long(
            entry,
            sl_init,
            sdf,
            buf=0.0,
            clamp_long_sl=lambda x: x,
            config=cfg,
        )
        self.assertIsNotNone(out)
        self.assertLessEqual((entry - float(out)) / entry, 0.05 + 1e-9)

    def test_widen_exceeds_cap_returns_none(self) -> None:
        cfg = StrategyConfig(
            min_sl_pct=0.02,
            koroush_min_stop_distance_pct=0.02,
            max_sl_widen_pct=0.03,
            swing_lookback=5,
        )
        lows = [100.0 - i * 0.5 for i in range(30)]
        sdf = self._make_sdf(lows)
        entry = 100.0
        sl_init = 99.9
        out = _widen_sl_min_risk_long(
            entry,
            sl_init,
            sdf,
            buf=0.0,
            clamp_long_sl=lambda x: x,
            config=cfg,
        )
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()
