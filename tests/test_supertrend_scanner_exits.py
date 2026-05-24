"""Supertrend 扫描器：持仓退出与 bar 去重行为。"""

from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

import supertrend_config as cfg
import supertrend_signal_scanner as scan
import supertrend_filters as filt
from supertrend_db import migrate_st_tables


class TestScannerExits(unittest.TestCase):
    def test_skip_entries_allows_exits_when_same_bar_entry(self) -> None:
        row = {"side": "LONG", "entry_bar_open_ms": 1000}
        state = (1000, 1)
        self.assertTrue(
            scan._should_skip_new_entries_this_bar(
                row, bar_open_ms=1000, state=state, buy=False, sell=False
            )
        )
        self.assertFalse(
            scan._should_skip_new_entries_this_bar(
                row, bar_open_ms=1000, state=state, buy=False, sell=True
            )
        )

    def test_hard_sl_exit_uses_sl_fill_on_wick(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        migrate_st_tables(cur)
        cur.execute(
            """
            INSERT INTO st_signals (
                recorded_at_utc, symbol, side, trend, signal_type,
                entry_price, sl_price, virtual_notional_usdt, meta_json
            ) VALUES ('t','X','LONG',1,'BUY',100,99,1000,'{}')
            """
        )
        open_row = cur.execute("SELECT * FROM st_signals").fetchone()
        stats: dict = {"closes": 0}
        events: list = []
        with patch.object(cfg, "ST_HARD_SL_USE_WICK", True):
            closed = scan._try_hard_sl_exit(
                cur,
                open_row,
                low_px=98.5,
                high_px=101.0,
                close_px=100.5,
                now_utc="2020-01-01T00:00:00Z",
                symbol="X",
                bar_open_ms=2000,
                stats=stats,
                events=events,
            )
        self.assertIsNotNone(closed)
        self.assertEqual(closed["exit_rule"], "hard_sl")
        self.assertEqual(closed["exit"], 99.0)
        self.assertEqual(stats.get("exit_hard_sl"), 1)

    def test_chop_cooldown_proactive_helper(self) -> None:
        with patch.multiple(cfg, ST_FILTER_ENABLED=True, ST_CHOP_MAX_FLIPS=2, ST_CHOP_COOLDOWN_BARS=5):
            until = filt.chop_cooldown_until_bar(3, 1000, 300_000)
            self.assertEqual(until, 1000 + 5 * 300_000)


if __name__ == "__main__":
    unittest.main()
