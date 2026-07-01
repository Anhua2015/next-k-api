"""KK vnpy sizing 单元测试（不依赖 vnpy_ctastrategy）。"""

from __future__ import annotations

import unittest

from orb.core.signals import compute_position_notional
from orb.kk.config import KKConfig
from orb.kk.vnpy.sizing import fixed_size_for_symbol, order_volume_to_notional


class TestKkVnpySizing(unittest.TestCase):
    def test_notional_cap(self):
        kk = KKConfig(equity_usdt=1000.0, risk_pct=0.01, max_notional_usdt=500.0)
        n = order_volume_to_notional(kk, 100.0)
        self.assertLessEqual(n, 500.0)
        self.assertGreater(n, 0)

    def test_fixed_size(self):
        kk = KKConfig(equity_usdt=1000.0, risk_pct=0.01, max_notional_usdt=1500.0)
        vol = fixed_size_for_symbol(kk, "COINUSDT", 250.0)
        self.assertGreater(vol, 0)

    def test_14u_matches_paper_safety(self):
        kk = KKConfig(equity_usdt=14.0, risk_pct=0.01)
        orb = kk.orb_session_cfg()
        entry = 100.0
        sl = entry * (1.0 - 0.008)
        paper = compute_position_notional(entry=entry, sl=sl, cfg=orb, bot_equity_usdt=14.0)
        vnpy = order_volume_to_notional(kk, entry, equity_usdt=14.0, orb_cfg=orb)
        self.assertAlmostEqual(paper, vnpy, places=2)


if __name__ == "__main__":
    unittest.main()
