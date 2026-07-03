"""King Keltner vnpy 策略会话/EOD 测试。"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest import mock

from orb.kk.vnpy.strategies.king_keltner_kk import KingKeltnerKkStrategy


class _Bar:
    def __init__(self, dt: datetime):
        self.datetime = dt
        self.close_price = 100.0
        self.high_price = 101.0
        self.low_price = 99.0


class TestKingKeltnerKkStrategy(unittest.TestCase):
    def _strategy(self) -> KingKeltnerKkStrategy:
        strat = KingKeltnerKkStrategy.__new__(KingKeltnerKkStrategy)
        strat.kk_rth_only = True
        strat.kk_eod_flat = True
        strat.kk_exit_hour = 15
        strat.kk_exit_minute = 55
        strat.pos = 1
        strat.vt_symbol = "COINUSDT_SWAP_BINANCE.GLOBAL"
        strat.trailing_percent = 0.8
        strat.intra_trade_high = 110.0
        strat.intra_trade_low = 90.0
        return strat

    def test_eod_uses_session_tz_not_utc(self):
        strat = self._strategy()
        # 2026-06-02 19:55 UTC = 15:55 America/New_York (EDT)
        bar = _Bar(datetime(2026, 6, 2, 19, 55, tzinfo=timezone.utc))
        with mock.patch.object(strat, "_session_cfg") as cfg_mock:
            cfg_mock.return_value.session_tz = "America/New_York"
            cfg_mock.return_value.session_open_time = "09:30"
            self.assertTrue(strat._is_eod_bar(bar))

        # 同 UTC 时刻在 15:54 ET 不应触发
        bar2 = _Bar(datetime(2026, 6, 2, 19, 54, tzinfo=timezone.utc))
        with mock.patch.object(strat, "_session_cfg") as cfg_mock:
            cfg_mock.return_value.session_tz = "America/New_York"
            cfg_mock.return_value.session_open_time = "09:30"
            self.assertFalse(strat._is_eod_bar(bar2))

    def test_trailing_sl_price_long(self):
        strat = self._strategy()
        sl = strat._trailing_sl_price()
        self.assertIsNotNone(sl)
        self.assertAlmostEqual(sl, 110.0 * (1 - 0.008), places=4)

    def test_past_entry_cutoff_noon_et(self):
        strat = self._strategy()
        strat.kk_no_entry_after_hour = 12
        strat.kk_no_entry_after_minute = 0
        bar = _Bar(datetime(2026, 6, 2, 16, 0, tzinfo=timezone.utc))  # 12:00 ET
        with mock.patch.object(strat, "_session_cfg") as cfg_mock:
            cfg_mock.return_value.session_tz = "America/New_York"
            cfg_mock.return_value.session_open_time = "09:30"
            self.assertTrue(strat._past_entry_cutoff(bar))
        bar2 = _Bar(datetime(2026, 6, 2, 15, 59, tzinfo=timezone.utc))  # 11:59 ET
        with mock.patch.object(strat, "_session_cfg") as cfg_mock:
            cfg_mock.return_value.session_tz = "America/New_York"
            cfg_mock.return_value.session_open_time = "09:30"
            self.assertFalse(strat._past_entry_cutoff(bar2))

    def test_on_5min_bar_after_cutoff_skips_entry_when_flat(self):
        strat = self._strategy()
        strat.pos = 0
        strat.kk_no_entry_after_hour = 12
        bar = _Bar(datetime(2026, 6, 2, 16, 5, tzinfo=timezone.utc))
        with mock.patch.object(strat, "_in_rth", return_value=True):
            with mock.patch.object(strat, "_past_entry_cutoff", return_value=True):
                with mock.patch.object(strat, "cancel_all") as cancel_mock:
                    with mock.patch(
                        "orb.kk.vnpy.strategies.king_keltner_kk.KingKeltnerStrategy.on_5min_bar"
                    ) as super_mock:
                        strat.on_5min_bar(bar)
        cancel_mock.assert_called_once()
        super_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
