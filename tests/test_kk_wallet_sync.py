"""KK wallet_sync 与 compound 持久化测试。"""

from __future__ import annotations

import sqlite3
import unittest

from orb.kk.config import KKConfig
from orb.kk.db import load_wallet, migrate_kk_tables, save_wallet
from orb.kk.wallet_sync import estimate_close_pnl, record_vnpy_fill


class TestKKWalletSync(unittest.TestCase):
    def test_estimate_close_pnl_long(self):
        kk = KKConfig(fee_maker_bps=2.0, fee_taker_bps=4.0)
        gross, fee, net = estimate_close_pnl(
            side="LONG",
            entry=100.0,
            exit_px=110.0,
            notional_usdt=100.0,
            kk=kk,
        )
        self.assertAlmostEqual(gross, 10.0, places=2)
        self.assertGreater(fee, 0)
        self.assertLess(net, gross)

    def test_compound_wallet_updates_on_close(self):
        import tempfile
        from pathlib import Path

        kk = KKConfig(equity_usdt=14.0, compound=True, fee_maker_bps=0.0, fee_taker_bps=0.0)
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "kk_test.db"
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            migrate_kk_tables(cur)
            save_wallet(cur, "INTCUSDT", 14.0, now_utc="2026-06-01T00:00:00Z")
            conn.commit()

            import accumulation_radar

            orig = accumulation_radar.init_db

            def _init():
                c = sqlite3.connect(str(db_path))
                return c

            try:
                accumulation_radar.init_db = _init
                wallet = record_vnpy_fill(
                    symbol="INTC",
                    event="close",
                    side="LONG",
                    price=110.0,
                    volume=1.0,
                    notional_usdt=100.0,
                    session_date="2026-06-01",
                    bar_ms=1,
                    kk=kk,
                    outcome="close",
                    pnl_usdt=10.0,
                    pnl_gross=10.0,
                    fee_usdt=0.0,
                )
                self.assertEqual(wallet, 24.0)
            finally:
                accumulation_radar.init_db = orig

            conn2 = sqlite3.connect(str(db_path))
            cur2 = conn2.cursor()
            self.assertEqual(load_wallet(cur2, "INTCUSDT", default=14.0), 24.0)
            conn2.close()
            conn.close()


if __name__ == "__main__":
    unittest.main()
