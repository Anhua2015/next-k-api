"""策略信号 API 测试。"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orb.vnpy import strategy_signals as ss


class TestStrategySignals(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _patch_db(self, *, row_factory=None):
        def _init_db():
            conn = sqlite3.connect(str(self.db_path))
            if row_factory is not None:
                conn.row_factory = row_factory
            return conn

        return mock.patch("accumulation_radar.init_db", side_effect=_init_db)

    def test_record_and_list_trading_orb(self):
        with self._patch_db():
            ss.record_strategy_signal(
                lane=ss.LANE_TRADING_ORB,
                symbol="btcusdt",
                side="LONG",
                entry_price=100.0,
                sl_price=95.0,
                tp_price=110.0,
                status="shadow",
                bar_ms=1700000000000,
            )
            out = ss.list_strategy_signals(lane=ss.LANE_TRADING_ORB, limit=10)
        self.assertTrue(out["ok"])
        self.assertEqual(out["count"], 1)
        sig = out["signals"][0]
        self.assertEqual(sig["symbol"], "BTCUSDT")
        self.assertEqual(sig["lane"], ss.LANE_TRADING_ORB)
        self.assertEqual(sig["status"], "shadow")

    def test_invalid_lane(self):
        out = ss.list_strategy_signals(lane="bad", limit=10)
        self.assertFalse(out["ok"])

    def test_list_without_row_factory_on_init_db(self):
        with self._patch_db(row_factory=None):
            ss.record_strategy_signal(
                lane=ss.LANE_ICT_2022,
                symbol="ethusdt",
                side="SHORT",
                entry_price=2000.0,
                status="emitted",
            )
            out = ss.list_strategy_signals(lane=ss.LANE_ICT_2022, limit=10)
        self.assertTrue(out["ok"])
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["signals"][0]["symbol"], "ETHUSDT")

    def test_dedup_key_uses_entry_and_time(self):
        a = ss._signal_dedup_key(
            {"symbol": "BTCUSDT", "side": "LONG", "entry_price": 100.0, "received_at": "2026-07-08T05:00:00Z"}
        )
        b = ss._signal_dedup_key(
            {"symbol": "BTCUSDT", "side": "LONG", "entry_price": 100.00001, "received_at": "2026-07-08T05:00:00Z"}
        )
        self.assertEqual(a, b)
