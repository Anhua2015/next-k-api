"""symbol_equity_usdt 测试。"""

from __future__ import annotations

import sqlite3
import unittest

from orb.kk.config import KKConfig
from orb.kk.db import migrate_kk_tables, save_wallet
from orb.kk.equity import symbol_equity_usdt


class TestKKEquity(unittest.TestCase):
    def test_compound_reads_wallet(self):
        kk = KKConfig(equity_usdt=14.0, compound=True)
        conn = sqlite3.connect(":memory:")
        cur = conn.cursor()
        migrate_kk_tables(cur)
        save_wallet(cur, "INTCUSDT", 28.5, now_utc="2026-06-01T00:00:00Z")
        self.assertEqual(symbol_equity_usdt(kk, "INTC", cur=cur), 28.5)
        self.assertEqual(symbol_equity_usdt(kk, "INTC"), 14.0)
        conn.close()


if __name__ == "__main__":
    unittest.main()
