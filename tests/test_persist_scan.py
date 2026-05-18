#!/usr/bin/env python3
"""persist_scan_results 落库逻辑测试。"""

from __future__ import annotations

import sqlite3
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Tuple

_API_ROOT = Path(__file__).resolve().parent.parent
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from zct_db_repositories import (
    PersistScanCallbacks,
    PersistScanLimits,
    SignalRepository,
)


@dataclass
class _FakeRow:
    symbol: str
    price: float = 100.0
    regime: str = "trend"
    play: str = "PLAY01_BREAKOUT"
    side: str = "LONG"
    confidence: str = "high"
    reasons: List[str] = None
    vwap: float = 100.0
    vwap_upper: float = 101.0
    vwap_lower: float = 99.0
    slope_bps: float = 5.0
    band_width_pct: float = 1.0
    vwap_crosses: int = 1
    ma_crosses: int = 1
    bands_wide: bool = True
    bands_tight: bool = False
    slope_steep: bool = True
    slope_flat: bool = False
    chop_score: str = "low"
    ref_levels: dict = None
    nearest_levels: list = None
    setup_level: int = 3
    position_vs_vwap: str = "above"
    vwap_cross_bucket: str = "0-3"
    entry_bar_open_ms: Optional[int] = 1
    sl_price: Optional[float] = 98.0
    tp_price: Optional[float] = 104.0
    r_unit: Optional[float] = 2.0
    paper_notional_usdt: Optional[float] = 1000.0

    def __post_init__(self) -> None:
        if self.reasons is None:
            self.reasons = []
        if self.ref_levels is None:
            self.ref_levels = {}
        if self.nearest_levels is None:
            self.nearest_levels = []


class PersistScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            """
            CREATE TABLE zct_vwap_signals (
                id INTEGER PRIMARY KEY,
                symbol TEXT UNIQUE NOT NULL,
                play TEXT, side TEXT, confidence TEXT, regime TEXT,
                entry_price REAL, entry_bar_open_ms INTEGER,
                sl_price REAL, tp_price REAL, r_unit REAL,
                virtual_notional_usdt REAL,
                vwap REAL, vwap_upper REAL, vwap_lower REAL,
                slope_bps REAL, band_width_pct REAL,
                vwap_crosses INTEGER, ma_crosses INTEGER, chop_score TEXT,
                bands_wide INTEGER, bands_tight INTEGER,
                slope_steep INTEGER, slope_flat INTEGER,
                ref_levels_json TEXT, nearest_levels_json TEXT,
                reasons_json TEXT, scan_params_json TEXT,
                setup_level INTEGER, vwap_cross_bucket TEXT, position_vs_vwap TEXT,
                outcome TEXT, outcome_at_utc TEXT, exit_price REAL, pnl_r REAL, pnl_usdt REAL,
                recorded_at_utc TEXT,
                manual_entry_price REAL, manual_exit_price REAL, manual_notes TEXT, notes TEXT
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
        self.repo = SignalRepository(
            self.conn,
            signals_table="zct_vwap_signals",
            settlements_table="zct_vwap_settlements",
        )
        self.settled: list[str] = []

    def tearDown(self) -> None:
        self.conn.close()

    def _callbacks(self) -> PersistScanCallbacks:
        def settle(cur, hold, r, at_utc) -> None:
            self.settled.append(str(hold[1]))

        return PersistScanCallbacks(
            is_open_hold_row=lambda r: r.side in ("LONG", "SHORT") and r.sl_price is not None,
            scan_supersedes_open_hold=lambda db_side, r: r.side != db_side,
            play_is_play01=lambda p: p and str(p).startswith("PLAY01"),
            play_is_play02=lambda p: p and str(p).startswith("PLAY02"),
            settle_supersede=settle,
        )

    def test_upsert_new_symbol(self) -> None:
        cur = self.conn.cursor()
        row = _FakeRow(symbol="BTCUSDT")
        stats = self.repo.persist_scan_results(
            cur,
            recorded_at_utc="2025-01-01T00:00:00Z",
            rows=[row],
            scan_params_json="{}",
            limits=PersistScanLimits(default_notional_usdt=1000.0),
            callbacks=self._callbacks(),
        )
        self.assertEqual(stats.written, 1)
        cur.execute("SELECT side, sl_price FROM zct_vwap_signals WHERE symbol='BTCUSDT'")
        side, sl = cur.fetchone()
        self.assertEqual(side, "LONG")
        self.assertEqual(sl, 98.0)

    def test_skip_existing_open_hold(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO zct_vwap_signals
            (symbol, side, sl_price, tp_price, entry_price, outcome, play)
            VALUES ('ETHUSDT', 'LONG', 90, 110, 100, NULL, 'PLAY01_X')
            """
        )
        self.conn.commit()
        stats = self.repo.persist_scan_results(
            cur,
            recorded_at_utc="2025-01-01T00:00:00Z",
            rows=[_FakeRow(symbol="ETHUSDT", side="LONG", sl_price=91.0, tp_price=111.0)],
            scan_params_json="{}",
            limits=PersistScanLimits(default_notional_usdt=1000.0),
            callbacks=self._callbacks(),
        )
        self.assertEqual(stats.written, 0)
        self.assertEqual(stats.skipped_open, 1)
        cur.execute("SELECT sl_price FROM zct_vwap_signals WHERE symbol='ETHUSDT'")
        self.assertEqual(cur.fetchone()[0], 90.0)

    def test_supersede_on_flip(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO zct_vwap_signals
            (id, symbol, side, sl_price, tp_price, entry_price, outcome, play, virtual_notional_usdt)
            VALUES (1, 'SOLUSDT', 'LONG', 90, 110, 100, NULL, 'PLAY01_X', 1000)
            """
        )
        self.conn.commit()
        stats = self.repo.persist_scan_results(
            cur,
            recorded_at_utc="2025-01-01T00:00:00Z",
            rows=[_FakeRow(symbol="SOLUSDT", side="SHORT", price=95.0, sl_price=96.0, tp_price=88.0)],
            scan_params_json="{}",
            limits=PersistScanLimits(default_notional_usdt=1000.0),
            callbacks=self._callbacks(),
        )
        self.assertEqual(stats.written, 1)
        self.assertEqual(self.settled, ["SOLUSDT"])
        cur.execute("SELECT side FROM zct_vwap_signals WHERE symbol='SOLUSDT'")
        self.assertEqual(cur.fetchone()[0], "SHORT")


if __name__ == "__main__":
    unittest.main()
