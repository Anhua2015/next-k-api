"""S2 独立 SQLite 库读写与 legacy 迁移。"""

from __future__ import annotations

import json
import os
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from s2_oi_funding_rate_scanner import (
    CST,
    _ensure_s2_funding_table,
    get_s2_funding_signals_for_api,
    init_s2_db,
    persist_strong_signals,
)


class TestS2Db(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = Path(os.getenv("TEMP", "/tmp")) / "test_s2_db"
        self._tmpdir.mkdir(parents=True, exist_ok=True)

    def test_s2_db_isolated_from_accumulation(self) -> None:
        data_dir = self._tmpdir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        acc = data_dir / "accumulation.db"
        s2_path = data_dir / "s2.db"

        recorded_at = datetime.now(CST).isoformat()
        acc_conn = sqlite3.connect(str(acc))
        _ensure_s2_funding_table(acc_conn)
        acc_conn.execute(
            """INSERT INTO s2_funding_signals (
                recorded_at, symbol, coin, price, price_chg_24h, prev_fr, current_fr,
                oi_change_pct, oi_segment_avgs_json, volume_usd, est_mcap_usd,
                has_spot, square_posts, square_views
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                recorded_at,
                "BTCUSDT",
                "BTC",
                100.0,
                1.0,
                0.0001,
                -0.0002,
                10.0,
                json.dumps([1.0, 2.0]),
                1e6,
                1e9,
                1,
                0,
                0,
            ),
        )
        acc_conn.commit()
        acc_conn.close()

        with patch.dict(os.environ, {"DATA_DIR": str(data_dir)}, clear=False):
            payload = get_s2_funding_signals_for_api(2)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["signals"][0]["symbol"], "BTCUSDT")
            self.assertTrue(s2_path.is_file())

    def test_persist_writes_to_s2_db(self) -> None:
        data_dir = self._tmpdir / "persist2"
        data_dir.mkdir(parents=True, exist_ok=True)
        s2_path = data_dir / "s2.db"
        if s2_path.is_file():
            s2_path.unlink()
        with patch.dict(os.environ, {"DATA_DIR": str(data_dir)}, clear=False):
            with patch(
                "s2_oi_funding_rate_scanner.get_market_caps",
                return_value={"TEST": 1e8},
            ), patch(
                "s2_oi_funding_rate_scanner.get_spot_symbols",
                return_value=set(),
            ), patch(
                "s2_oi_funding_rate_scanner.get_square_discussion",
                return_value=(0, 0),
            ):
                persist_strong_signals(
                    [
                        {
                            "symbol": "TESTUSDT",
                            "price": 1.0,
                            "price_chg_24h": 2.0,
                            "prev_fr": 0.0001,
                            "current_fr": -0.0002,
                            "oi_change": 12.0,
                            "oi_segments": [1.0, 2.0],
                            "volume": 500000.0,
                        }
                    ]
                )
            conn = init_s2_db()
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM s2_funding_signals WHERE symbol='TESTUSDT'")
            self.assertEqual(int(cur.fetchone()[0]), 1)
            conn.close()


if __name__ == "__main__":
    unittest.main()
