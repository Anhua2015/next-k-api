"""ORB live_exec 载荷构建测试。"""

from __future__ import annotations

import unittest
from dataclasses import replace

from orb.core.config import OrbConfig
from orb.core.fvg import uses_fvg_entry
from orb.core.live_exec import build_close_payload, build_open_payload, fvg_api_signal_id
from orb.core.signals import OrbSignal


class TestOrbLiveExec(unittest.TestCase):
    def test_open_payload_uses_default_leverage(self):
        cfg = replace(OrbConfig(), live_enabled=True)
        sig = OrbSignal(
            symbol="COINUSDT",
            price=160.0,
            side="LONG",
            play="ORB_BREAKOUT_LONG",
            confidence="high",
            reasons=[],
            sl_price=159.0,
            tp_price=None,
            session_date="2026-06-09",
            entry_bar_open_ms=1_700_000_000_000,
            paper_notional_usdt=2500.0,
        )
        p = build_open_payload(sig, cfg)
        self.assertEqual(p["leverage"], 10.0)
        self.assertAlmostEqual(p["margin_usdt"], 250.0)
        self.assertAlmostEqual(p["margin_usdt"] * p["leverage"], 2500.0)

    def test_open_payload_margin_from_notional(self):
        cfg = replace(OrbConfig(), live_enabled=True, leverage=5.0, margin_usdt=100.0)
        sig = OrbSignal(
            symbol="QQQUSDT",
            price=400.0,
            side="LONG",
            play="ORB_BREAKOUT_LONG",
            confidence="high",
            reasons=[],
            sl_price=395.0,
            tp_price=None,
            session_date="2026-06-09",
            entry_bar_open_ms=1_700_000_000_000,
            paper_notional_usdt=500.0,
        )
        p = build_open_payload(sig, cfg)
        self.assertEqual(p["source"], "orb")
        self.assertEqual(p["symbol"], "QQQUSDT")
        self.assertEqual(p["action"], "open")
        self.assertAlmostEqual(p["margin_usdt"], 100.0)
        self.assertEqual(p["leverage"], 5.0)
        self.assertIsNone(p["tp_price"])

    def test_close_payload_session_close_uses_market(self):
        p = build_close_payload("COINUSDT", "SHORT", close_price=155.0, tag="session_close")
        self.assertEqual(p["action"], "close")
        self.assertEqual(p["side"], "SHORT")
        self.assertNotIn("close_price", p)
        self.assertEqual(p["api_signal_id"], "orb:close:COINUSDT:session_close")

    def test_close_payload_loss_keeps_limit_price(self):
        p = build_close_payload("COINUSDT", "SHORT", close_price=155.0, tag="loss", signal_id=99)
        self.assertAlmostEqual(p["close_price"], 155.0)
        self.assertEqual(p["api_signal_id"], "orb:close:COINUSDT:99:loss")

    def test_fvg_open_payload_uses_limit_and_stable_api_id(self):
        cfg = replace(OrbConfig(), live_enabled=True, entry_fill="fvg_prox")
        sig = OrbSignal(
            symbol="TSLAUSDT",
            price=200.0,
            side="LONG",
            play="ORB_BREAKOUT_LONG",
            confidence="high",
            reasons=[],
            or_high=201.0,
            or_low=195.0,
            sl_price=199.0,
            tp_price=None,
            session_date="2026-06-09",
            entry_bar_open_ms=1_700_000_000_000,
            r_unit=0.5,
            paper_notional_usdt=140.0,
        )
        p = build_open_payload(sig, cfg)
        self.assertEqual(p["entry_type"], "LIMIT")
        self.assertEqual(p["api_signal_id"], fvg_api_signal_id(sig))
        self.assertEqual(p["or_high"], 201.0)
        self.assertEqual(p["sl_risk_dist"], 0.5)

    def test_fvg_api_id_uses_confirm_bar_after_fill_merge(self):
        cfg = replace(OrbConfig(), entry_fill="fvg_prox")
        sig = OrbSignal(
            symbol="TSLAUSDT",
            price=198.0,
            side="LONG",
            play="ORB_BREAKOUT_LONG",
            confidence="high",
            reasons=[],
            session_date="2026-06-09",
            entry_bar_open_ms=1_700_000_060_000,
            fvg_confirm_bar_ms=1_700_000_000_000,
        )
        self.assertEqual(
            fvg_api_signal_id(sig),
            "orb:fvg:TSLAUSDT:2026-06-09:1700000000000",
        )
        self.assertTrue(uses_fvg_entry(cfg))


if __name__ == "__main__":
    unittest.main()
