#!/usr/bin/env python3
"""SignalRepository 内存 SQLite 烟测。"""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parent.parent
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from zct_db_repositories import SignalRepository


class SignalRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            """
            CREATE TABLE zct_vwap_signals (
                id INTEGER PRIMARY KEY,
                symbol TEXT UNIQUE,
                play TEXT,
                side TEXT,
                outcome TEXT,
                sl_price REAL,
                tp_price REAL,
                entry_price REAL,
                virtual_notional_usdt REAL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE zct_vwap_settlements (
                id INTEGER PRIMARY KEY,
                settled_at_utc TEXT,
                pnl_usdt REAL
            )
            """
        )
        self.repo = SignalRepository(
            self.conn,
            signals_table="zct_vwap_signals",
            settlements_table="zct_vwap_settlements",
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_open_position_counts(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO zct_vwap_signals
            (symbol, play, side, outcome, sl_price, tp_price, entry_price)
            VALUES ('BTCUSDT', 'PLAY01_BREAKOUT', 'LONG', NULL, 1.0, 2.0, 1.5)
            """
        )
        cur.execute(
            """
            INSERT INTO zct_vwap_signals
            (symbol, play, side, outcome, sl_price, tp_price, entry_price)
            VALUES ('ETHUSDT', 'PLAY02_BREAKDOWN', 'SHORT', NULL, 1.0, 2.0, 1.5)
            """
        )
        self.conn.commit()
        self.assertEqual(self.repo.count_open_positions(cur), 2)
        self.assertEqual(self.repo.count_open_play_family(cur, "PLAY01"), 1)
        syms = self.repo.fetch_symbols_with_open_positions(cur)
        self.assertEqual(syms, {"BTCUSDT", "ETHUSDT"})


if __name__ == "__main__":
    unittest.main()
