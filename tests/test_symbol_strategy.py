"""Per-symbol ORB strategy config."""

from __future__ import annotations

import unittest

from orb.core.config import OrbConfig
from orb.core.symbol_strategy import config_for_symbol, strategy_env_path, ticker_from_symbol


class TestSymbolStrategy(unittest.TestCase):
    def test_ticker_from_symbol(self):
        self.assertEqual(ticker_from_symbol("COINUSDT"), "COIN")
        self.assertEqual(ticker_from_symbol("CRCLUSDT"), "CRCL")

    def test_strategy_env_paths_exist(self):
        self.assertTrue(strategy_env_path("COINUSDT").is_file())
        self.assertTrue(strategy_env_path("CRCLUSDT").is_file())
        self.assertTrue(strategy_env_path("TSLAUSDT").is_file())

    def test_coin_or10_crcl_or5_tsla_or5(self):
        base = OrbConfig.from_env()
        coin = config_for_symbol("COINUSDT", base=base)
        crcl = config_for_symbol("CRCLUSDT", base=base)
        tsla = config_for_symbol("TSLAUSDT", base=base)
        self.assertEqual(coin.or_minutes, 10)
        self.assertAlmostEqual(coin.risk_pct, 0.03)
        self.assertEqual(crcl.or_minutes, 5)
        self.assertAlmostEqual(crcl.risk_pct, 0.03)
        self.assertEqual(tsla.or_minutes, 5)
        self.assertAlmostEqual(tsla.risk_pct, 0.03)

    def test_unknown_symbol_falls_back_to_base(self):
        base = OrbConfig.from_env()
        other = config_for_symbol("ZZZZUSDT", base=base)
        self.assertEqual(other.or_minutes, base.or_minutes)
        self.assertEqual(other.risk_pct, base.risk_pct)


if __name__ == "__main__":
    unittest.main()
