"""Pending OCO sync / promote tests."""

from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

from orb.core.config import OrbConfig
from orb.core.db import migrate_orb_tables
from orb.core.live_exec import sync_live_pending_entries
from orb.core.protocol_client import LIVE_PENDING_OCO_NOTE


class TestSyncPendingOco(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        migrate_orb_tables(self.conn)
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO orb_signals (
                recorded_at_utc, updated_at_utc, symbol, play, side, confidence,
                entry_price, entry_bar_open_ms, sl_price, tp_price, virtual_notional_usdt,
                session_date, notes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "2026-06-09T14:45:00Z",
                "2026-06-09T14:45:00Z",
                "COINUSDT",
                "ORB_PREPLACE_ARM",
                "LONG",
                "high",
                100.0,
                1_700_000_000_000,
                99.0,
                None,
                850.0,
                "2026-06-09",
                LIVE_PENDING_OCO_NOTE,
            ),
        )
        self.conn.commit()
        self.cfg = OrbConfig(live_enabled=True)

    def tearDown(self) -> None:
        self.conn.close()

    @patch("orb.core.live_exec.reconcile_pending_entries")
    @patch("orb.core.live_exec.lookup_signal")
    @patch("orb.core.live_exec.live_enabled", return_value=True)
    def test_short_fill_promotes_sl_and_notional(self, _live, mock_lookup, _reconcile) -> None:
        def _lookup(*, source: str, api_signal_id: str):
            if api_signal_id.endswith(":LONG"):
                return {"status": "cancelled", "side": "LONG"}
            if api_signal_id.endswith(":SHORT"):
                return {
                    "status": "traded",
                    "side": "SHORT",
                    "sl_price": 101.0,
                    "tp_price": None,
                    "result": {
                        "entry_price": 100.2,
                        "notional_usdt": 820.0,
                        "side": "SHORT",
                    },
                }
            return None

        mock_lookup.side_effect = _lookup
        changed = sync_live_pending_entries(self.conn, self.cfg)
        self.assertEqual(changed, 1)
        cur = self.conn.cursor()
        cur.execute(
            "SELECT side, entry_price, sl_price, virtual_notional_usdt, notes FROM orb_signals WHERE symbol=?",
            ("COINUSDT",),
        )
        side, entry, sl, notion, notes = cur.fetchone()
        self.assertEqual(side, "SHORT")
        self.assertAlmostEqual(entry, 100.2)
        self.assertAlmostEqual(sl, 101.0)
        self.assertAlmostEqual(notion, 820.0)
        self.assertIsNone(notes)

    @patch("orb.core.live_exec.reconcile_pending_entries")
    @patch("orb.core.live_exec.lookup_signal")
    @patch("orb.core.live_exec.live_enabled", return_value=True)
    def test_peer_cancelled_does_not_delete_before_other_leg_checked(self, _live, mock_lookup, _reconcile) -> None:
        calls = {"n": 0}

        def _lookup(*, source: str, api_signal_id: str):
            calls["n"] += 1
            if api_signal_id.endswith(":LONG"):
                return {"status": "cancelled", "side": "LONG"}
            if api_signal_id.endswith(":SHORT"):
                return {"status": "submitted", "side": "SHORT"}
            return None

        mock_lookup.side_effect = _lookup
        changed = sync_live_pending_entries(self.conn, self.cfg)
        self.assertEqual(changed, 0)
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM orb_signals WHERE symbol=?", ("COINUSDT",))
        self.assertEqual(cur.fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
