"""King Keltner 实盘信号构建与 ingest 判定测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from orb.core.live_exec import live_ingest_succeeded
from orb.kk.config import KKConfig
from orb.kk.live_exec import (
    SOURCE_KK,
    bootstrap_sl_price,
    build_close_payload,
    build_open_payload,
    live_enabled,
    notify_trailing_sl,
)


class TestKKLiveExec(unittest.TestCase):
    def test_bootstrap_sl_long(self):
        sl = bootstrap_sl_price(side="LONG", entry=100.0, sl=0.0)
        self.assertAlmostEqual(sl, 99.2, places=4)

    def test_bootstrap_sl_uses_existing(self):
        sl = bootstrap_sl_price(side="LONG", entry=100.0, sl=98.5)
        self.assertAlmostEqual(sl, 98.5, places=4)

    def test_open_payload_market_with_sl(self):
        kk = KKConfig(live_enabled=True, live_leverage=5.0, equity_usdt=1000.0)
        orb_cfg = kk.orb_session_cfg()
        payload = build_open_payload(
            symbol="COINUSDT",
            side="LONG",
            entry_price=250.0,
            notional_usdt=1200.0,
            session_date="2026-06-23",
            bar_ms=1_700_000_000_000,
            sl_price=None,
            kk=kk,
            orb_cfg=orb_cfg,
        )
        self.assertEqual(payload["source"], SOURCE_KK)
        self.assertEqual(payload["entry_type"], "MARKET")
        self.assertEqual(payload["action"], "open")
        self.assertIsNotNone(payload["sl_price"])
        self.assertAlmostEqual(float(payload["sl_price"]), 250.0 * 0.992, places=2)
        self.assertAlmostEqual(float(payload["margin_usdt"]), 240.0, places=1)

    def test_close_payload_eod_omits_limit_price(self):
        payload = build_close_payload(
            symbol="COINUSDT",
            side="LONG",
            session_date="2026-06-23",
            bar_ms=1_700_000_000_000,
            outcome="eod",
            close_price=255.0,
        )
        self.assertEqual(payload["action"], "close")
        self.assertNotIn("close_price", payload)

    def test_close_payload_stop_includes_price(self):
        payload = build_close_payload(
            symbol="COINUSDT",
            side="LONG",
            session_date="2026-06-23",
            bar_ms=1_700_000_000_000,
            outcome="loss",
            close_price=248.0,
        )
        self.assertEqual(payload["close_price"], 248.0)

    def test_live_enabled_requires_protocol_url(self):
        kk = KKConfig(live_enabled=True)
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(live_enabled(kk))
        with patch.dict("os.environ", {"PROTOCOL_API_URL": "http://127.0.0.1:8001"}, clear=False):
            self.assertTrue(live_enabled(kk))

    def test_leverage_defaults_to_5(self):
        from orb.kk.live_exec import _leverage

        kk = KKConfig(live_enabled=True, live_leverage=0.0)
        self.assertEqual(_leverage(kk, kk.orb_session_cfg()), 5.0)

    @patch("orb.kk.live_exec.update_protective_sl")
    def test_notify_trailing_sl_success(self, mock_update):
        mock_update.return_value = {"ok": True, "sl_price": 99.0}
        result = notify_trailing_sl(symbol="COINUSDT", side="LONG", sl_price=99.0)
        self.assertTrue(live_ingest_succeeded(result))

    @patch("orb.kk.live_exec.update_protective_sl")
    def test_notify_trailing_sl_failure(self, mock_update):
        mock_update.return_value = {"ok": False, "error": "no_position"}
        result = notify_trailing_sl(symbol="COINUSDT", side="LONG", sl_price=99.0)
        self.assertFalse(live_ingest_succeeded(result))


if __name__ == "__main__":
    unittest.main()
