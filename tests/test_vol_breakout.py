"""CrackingMarkets vol_breakout 信号测试。"""

from __future__ import annotations

import unittest

import pandas as pd

from orb.core.config import OrbConfig
from orb.core.vol_breakout import (
    classify_vol_breakout_signal,
    compute_vol_breakout_levels,
    is_vol_breakout_mode,
    prev_rth_close_asof,
)


def _daily_rows(closes: list[float], *, start_ms: int = 1_700_000_000_000) -> pd.DataFrame:
    rows = []
    for i, c in enumerate(closes):
        t = start_ms + i * 86_400_000
        rows.append({"open_time": t, "open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 100})
    return pd.DataFrame(rows)


class TestVolBreakout(unittest.TestCase):
    def test_mode_flag(self):
        self.assertTrue(is_vol_breakout_mode(OrbConfig(entry_mode="vol_breakout")))
        self.assertFalse(is_vol_breakout_mode(OrbConfig(entry_mode="breakout")))

    def test_levels(self):
        cfg = OrbConfig(entry_mode="vol_breakout", atr_breakout_mult=0.33)
        pack = compute_vol_breakout_levels(prev_close=100.0, daily_atr=10.0, cfg=cfg)
        self.assertIsNotNone(pack)
        upper, lower, width_pct = pack
        self.assertAlmostEqual(upper, 103.3)
        self.assertAlmostEqual(lower, 96.7)
        self.assertAlmostEqual(width_pct, 6.6, places=1)

    def test_prev_close(self):
        daily = _daily_rows([90.0, 95.0, 100.0])
        asof = daily["open_time"].iloc[-1] + 86_400_000
        px = prev_rth_close_asof(daily, int(asof), tz="UTC")
        self.assertAlmostEqual(px, 100.0)

    def test_config_from_env_mult(self):
        import os

        saved = os.environ.get("ORB_ATR_BREAKOUT_MULT")
        os.environ["ORB_ATR_BREAKOUT_MULT"] = "0.5"
        try:
            cfg = OrbConfig.from_env()
            self.assertAlmostEqual(cfg.atr_breakout_mult, 0.5)
        finally:
            if saved is None:
                os.environ.pop("ORB_ATR_BREAKOUT_MULT", None)
            else:
                os.environ["ORB_ATR_BREAKOUT_MULT"] = saved


if __name__ == "__main__":
    unittest.main()
