"""CTA 实盘撮合测试。"""

from __future__ import annotations

import unittest

from orb.cta.engine import CtaBacktestConfig, CtaContext, Position, _try_fills
from orb.cta.execution import entry_fill_px, market_exit_fill_px, stop_exit_fill_px
from orb.cta.strategies import cta_config_for_strategy
from orb.core.config import OrbConfig


class TestCtaExecution(unittest.TestCase):
    def test_entry_slip_long(self):
        px = entry_fill_px(1, 100.0, 5.0)
        self.assertAlmostEqual(px, 100.05, places=4)

    def test_stop_exit_gap_down(self):
        px = stop_exit_fill_px(1, 100.0, bar_open=98.0, slip_bps=5.0)
        self.assertAlmostEqual(px, 98.0 * (1 - 5 / 10000), places=4)

    def test_market_exit_eod_long(self):
        px = market_exit_fill_px(1, 200.0, 5.0)
        self.assertAlmostEqual(px, 199.9, places=4)

    def test_king_keltner_defaults_include_slip(self):
        cfg = cta_config_for_strategy("king_keltner")
        self.assertEqual(cfg.entry_fee_mode, "stop")
        self.assertAlmostEqual(cfg.slip_bps_entry, 5.0)
        self.assertAlmostEqual(cfg.slip_bps_exit, 5.0)

    def test_stop_entry_applies_entry_slip(self):
        orb_cfg = OrbConfig.from_env()
        cfg = cta_config_for_strategy("king_keltner")
        ctx = CtaContext(cfg=cfg, orb_cfg=orb_cfg, wallet=1000.0)
        ctx.set_entry_stops(100.0, 90.0)
        bar = {"open": 99.0, "high": 101.0, "low": 99.0, "close": 100.5, "open_time": 60_000}
        _try_fills(ctx, bar, cfg)
        self.assertEqual(ctx.pos.side, 1)
        self.assertAlmostEqual(ctx.pos.entry, entry_fill_px(1, 100.0, cfg.slip_bps_entry), places=4)


if __name__ == "__main__":
    unittest.main()
