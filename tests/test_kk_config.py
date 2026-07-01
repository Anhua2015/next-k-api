"""King Keltner lane 配置与 DB 测试。"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from orb.kk.config import KKConfig
from orb.kk.db import count_open_kk_positions, migrate_kk_tables, save_state_json
from orb.kk.paths import resolve_kk_symbols_path


class TestKKConfig(unittest.TestCase):
    def test_default_symbols_file(self):
        kk = KKConfig.from_env()
        self.assertTrue(str(kk.symbols_file).replace("\\", "/").endswith("config/kk/symbols.txt"))
        self.assertEqual(kk.lane, "king_keltner")
        self.assertTrue(kk.rth_only)
        self.assertTrue(kk.eod_flat)

    def test_symbol_list_top7_pool(self):
        kk = KKConfig.from_env()
        syms = kk.symbol_list()
        self.assertEqual(len(syms), 7)
        expected = [
            "INTCUSDT",
            "SOXLUSDT",
            "HOODUSDT",
            "CRCLUSDT",
            "COINUSDT",
            "SNDKUSDT",
            "MSTRUSDT",
        ]
        self.assertEqual(syms, expected)
        for removed in ("PLTRUSDT", "TSLAUSDT", "NVDAUSDT", "QQQUSDT"):
            self.assertNotIn(removed, syms)

    def test_default_equity_per_bot(self):
        saved = os.environ.pop("KK_EQUITY_USDT", None)
        try:
            os.environ.pop("KK_EQUITY_USDT", None)
            kk = KKConfig.from_env()
            self.assertEqual(kk.equity_usdt, 14.0)
        finally:
            if saved is None:
                os.environ.pop("KK_EQUITY_USDT", None)
            else:
                os.environ["KK_EQUITY_USDT"] = saved

    def test_env_symbols_override_file(self):
        saved = {k: os.environ.pop(k, None) for k in ("KK_SYMBOLS", "KK_SYMBOLS_FILE")}
        try:
            os.environ["KK_SYMBOLS"] = "TSLA,COIN"
            kk = KKConfig.from_env()
            self.assertEqual(kk.symbol_list(), ["TSLAUSDT", "COINUSDT"])
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_live_defaults_max_notional(self):
        saved = {k: os.environ.pop(k, None) for k in ("KK_LIVE_ENABLED", "KK_MAX_NOTIONAL_USDT")}
        try:
            os.environ["KK_LIVE_ENABLED"] = "1"
            os.environ.pop("KK_MAX_NOTIONAL_USDT", None)
            kk = KKConfig.from_env()
            self.assertEqual(kk.max_notional_usdt, 0.0)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_live_defaults_compound_on(self):
        saved = {k: os.environ.pop(k, None) for k in ("KK_LIVE_ENABLED", "KK_COMPOUND", "KK_EOD_FLAT")}
        try:
            os.environ["KK_LIVE_ENABLED"] = "1"
            os.environ.pop("KK_COMPOUND", None)
            os.environ.pop("KK_EOD_FLAT", None)
            kk = KKConfig.from_env()
            self.assertTrue(kk.compound)
            self.assertTrue(kk.eod_flat)
            self.assertEqual(kk.max_notional_usdt, 0.0)
            self.assertEqual(kk.max_open_positions, 7)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_compound_can_be_disabled(self):
        saved = os.environ.pop("KK_COMPOUND", None)
        try:
            os.environ["KK_COMPOUND"] = "0"
            kk = KKConfig.from_env()
            self.assertFalse(kk.compound)
        finally:
            if saved is None:
                os.environ.pop("KK_COMPOUND", None)
            else:
                os.environ["KK_COMPOUND"] = saved

    def test_vnpy_engine_default(self):
        saved = {k: os.environ.pop(k, None) for k in ("KK_ENGINE", "KK_VNPY_ENABLED", "KK_LIVE_ENABLED")}
        try:
            os.environ.pop("KK_VNPY_ENABLED", None)
            os.environ.pop("KK_LIVE_ENABLED", None)
            kk = KKConfig.from_env()
            self.assertEqual(kk.engine, "vnpy")
            self.assertTrue(kk.is_vnpy_engine())
            self.assertFalse(kk.live_enabled)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_paper_engine(self):
        saved = os.environ.pop("KK_ENGINE", None)
        try:
            os.environ["KK_ENGINE"] = "paper"
            kk = KKConfig.from_env()
            self.assertTrue(kk.is_paper_engine())
            self.assertFalse(kk.is_vnpy_engine())
        finally:
            if saved is None:
                os.environ.pop("KK_ENGINE", None)
            else:
                os.environ["KK_ENGINE"] = saved

    def test_vnpy_enabled_env(self):
        saved = os.environ.pop("KK_VNPY_ENABLED", None)
        try:
            os.environ["KK_VNPY_ENABLED"] = "1"
            kk = KKConfig.from_env()
            self.assertTrue(kk.vnpy_enabled)
        finally:
            if saved is None:
                os.environ.pop("KK_VNPY_ENABLED", None)
            else:
                os.environ["KK_VNPY_ENABLED"] = saved

    def test_resolve_kk_symbols_path_exists(self):
        self.assertTrue(resolve_kk_symbols_path().is_file())


class TestKKDb(unittest.TestCase):
    def test_migrate_and_count_open_positions(self):
        conn = sqlite3.connect(":memory:")
        cur = conn.cursor()
        migrate_kk_tables(cur)
        self.assertEqual(count_open_kk_positions(cur, session_date="2026-06-01"), 0)
        save_state_json(
            cur,
            "COINUSDT",
            "2026-06-01",
            state={"ctx": {"pos": {"side": 1, "entry": 100.0}}},
            last_bar_ms=1,
        )
        self.assertEqual(count_open_kk_positions(cur, session_date="2026-06-01"), 1)
        conn.close()


if __name__ == "__main__":
    unittest.main()
