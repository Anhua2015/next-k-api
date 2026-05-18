#!/usr/bin/env python3
"""结算 UPDATE / 查询 Repository 烟测。"""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parent.parent
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from zct_db_repositories import SignalRepository


class ResolveRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            """
            CREATE TABLE zct_vwap_signals (
                id INTEGER PRIMARY KEY,
                symbol TEXT,
                side TEXT, play TEXT,
                entry_price REAL, sl_price REAL, tp_price REAL,
                entry_bar_open_ms INTEGER,
                virtual_notional_usdt REAL,
                outcome TEXT, outcome_at_utc TEXT, exit_price REAL,
                pnl_r REAL, pnl_usdt REAL, notes TEXT
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE zct_vwap_settlements (
                id INTEGER PRIMARY KEY,
                settled_at_utc TEXT, signal_id INTEGER, symbol TEXT,
                side TEXT, play TEXT, outcome TEXT,
                entry_price REAL, exit_price REAL, pnl_r REAL, pnl_usdt REAL,
                virtual_notional_usdt REAL
            )
            """
        )
        self.conn.execute(
            """
            INSERT INTO zct_vwap_signals
            (id, symbol, side, play, entry_price, sl_price, tp_price, entry_bar_open_ms,
             virtual_notional_usdt, outcome)
            VALUES (1, 'BTCUSDT', 'LONG', 'PLAY01_X', 100, 95, 110, 1000, 1000, NULL)
            """
        )
        self.conn.commit()
        self.repo = SignalRepository(
            self.conn,
            signals_table="zct_vwap_signals",
            settlements_table="zct_vwap_settlements",
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_fetch_and_update_resolved(self) -> None:
        cur = self.conn.cursor()
        rows = self.repo.fetch_open_signals_for_resolve(cur, default_notional_usdt=1000.0)
        self.assertEqual(len(rows), 1)
        n = self.repo.update_resolved_signal(
            cur,
            1,
            outcome="win",
            outcome_at_utc="2025-01-01T00:00:00Z",
            exit_price=110.0,
            pnl_r=1.0,
            pnl_usdt=10.0,
            note="resolved:auto",
        )
        self.assertEqual(n, 1)
        cur.execute("SELECT outcome, pnl_usdt FROM zct_vwap_signals WHERE id=1")
        outcome, pnl = cur.fetchone()
        self.assertEqual(outcome, "win")
        self.assertEqual(pnl, 10.0)


if __name__ == "__main__":
    unittest.main()
