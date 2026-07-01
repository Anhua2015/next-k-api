"""King Keltner 与 vnpy 原版对齐测试。"""

from __future__ import annotations

import unittest

import pandas as pd

from orb.cta import indicators as ind
from orb.cta.engine import CtaBacktestConfig, CtaContext, Position
from orb.cta.strategies import KK_DEV, KK_LENGTH, KK_TRAILING_PCT, cta_config_for_strategy, king_keltner_on_bar
from orb.core.config import OrbConfig


class TestKingKeltnerVnpyAlign(unittest.TestCase):
    def test_keltner_uses_sma_mid(self):
        df = pd.DataFrame(
            {
                "open": [10.0] * 25,
                "high": [11.0] * 25,
                "low": [9.0] * 25,
                "close": [float(i) for i in range(25)],
                "volume": [100.0] * 25,
            }
        )
        up, down = ind.keltner(df, KK_LENGTH, KK_DEV)
        mid = ind.sma(df["close"], KK_LENGTH)
        atr_v = ind.atr(df, KK_LENGTH)
        self.assertAlmostEqual(float(up.iloc[-1]), float(mid.iloc[-1] + KK_DEV * atr_v.iloc[-1]), places=6)
        self.assertAlmostEqual(float(down.iloc[-1]), float(mid.iloc[-1] - KK_DEV * atr_v.iloc[-1]), places=6)

    def test_cta_config_overrides(self):
        cfg = cta_config_for_strategy("king_keltner")
        self.assertEqual(cfg.entry_stop_sl_pct, 0.0)
        self.assertAlmostEqual(cfg.entry_risk_sl_pct, KK_TRAILING_PCT / 100.0)
        self.assertFalse(cfg.bar_intra_update)

    def test_long_trailing_on_5m_bar(self):
        orb_cfg = OrbConfig.from_env()
        cta_cfg = cta_config_for_strategy("king_keltner")
        ctx = CtaContext(cfg=cta_cfg, orb_cfg=orb_cfg, wallet=1000.0)
        ctx.pos = Position(side=1, entry=100.0, sl=0.0, notional=100.0, entry_ms=0)
        ctx.intra_high = 100.0
        ctx.intra_low = 99.0
        ctx.state["buf"] = {"rows": [{"open_time": i, "open": 10, "high": 10, "low": 9, "close": 10, "volume": 1} for i in range(25)]}
        ctx.state["kk"] = {"last_bucket": 0, "bar": {"open": 100, "high": 105, "low": 98, "close": 104, "open_time": 300_000}}
        row = pd.Series({"open": 104, "high": 106, "low": 103, "close": 105, "open_time": 600_000})
        king_keltner_on_bar(ctx, row, 600_000)
        self.assertAlmostEqual(ctx.intra_high, 105.0)
        self.assertAlmostEqual(ctx.intra_low, 98.0)
        expected_stop = 105.0 * (1.0 - KK_TRAILING_PCT / 100.0)
        self.assertAlmostEqual(ctx.pos.sl, expected_stop, places=4)


if __name__ == "__main__":
    unittest.main()
