"""Moss2 QuantStats 报告工具测试（不强制安装 quantstats）。"""

import sqlite3
import unittest
from datetime import datetime, timezone

import pandas as pd

from moss2.reports.quantstats_report import (
    equity_list_to_daily_returns,
    quantstats_available,
    settlements_to_daily_returns,
)


class Moss2QuantstatsTests(unittest.TestCase):
    def test_equity_to_returns(self):
        equity = [10000.0, 10050.0, 10020.0, 10100.0, 10080.0, 10200.0]
        ret = equity_list_to_daily_returns(equity, bar_minutes=15)
        self.assertGreaterEqual(len(ret), 1)
        self.assertTrue(ret.std() > 0 or ret.abs().sum() >= 0)

    def test_settlements_returns(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """CREATE TABLE moss2_settlements (
            settled_at_utc TEXT, pnl_usdt REAL, virtual_notional_usdt REAL,
            outcome TEXT, profile_id INTEGER)"""
        )
        conn.execute(
            "INSERT INTO moss2_settlements VALUES (?,?,?,?,?)",
            ("2026-06-01T12:00:00", 50.0, 10000.0, "win", 1),
        )
        conn.execute(
            "INSERT INTO moss2_settlements VALUES (?,?,?,?,?)",
            ("2026-06-02T12:00:00", -30.0, 10000.0, "loss", 1),
        )
        ret, meta = settlements_to_daily_returns(conn, 1, capital=10000.0)
        self.assertEqual(meta["trades"], 2)
        self.assertGreaterEqual(len(ret), 1)

    @unittest.skipUnless(quantstats_available(), "quantstats not installed")
    def test_generate_tearsheet_smoke(self):
        from moss2.reports.quantstats_report import generate_moss2_tearsheet

        idx = pd.date_range("2025-01-01", periods=60, freq="D")
        rng = pd.Series(0.001, index=idx) + pd.Series(
            [0.002, -0.001, 0.0015, -0.0005] * 15, index=idx
        )
        out = generate_moss2_tearsheet(
            returns=rng,
            title="test",
            filename_stem="test_moss2",
        )
        self.assertTrue(out.get("ok"))
        self.assertTrue(out.get("path"))


if __name__ == "__main__":
    unittest.main()
