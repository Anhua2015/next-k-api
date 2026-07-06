"""King Keltner vnpy 回测策略与 session 边界测试。"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

import pandas as pd

from orb.core.config import OrbConfig
from orb.kk.vnpy.backtest import _bar_ms, _prepare_bars, bar_symbol_from_vt, session_bounds_for_date
from orb.kk.vnpy.binance_gateway import kk_vt_symbol

ensure = __import__("orb.kk.vnpy.bootstrap", fromlist=["ensure_vnpy_path"]).ensure_vnpy_path
ensure()

from vnpy.trader.constant import Exchange, Interval  # noqa: E402
from vnpy.trader.object import BarData  # noqa: E402


class TestKkVnpyBacktest(unittest.TestCase):
    def test_bar_symbol_from_vt(self) -> None:
        self.assertEqual(bar_symbol_from_vt("INTCUSDT_SWAP_BINANCE.GLOBAL"), "INTCUSDT_SWAP_BINANCE")

    def test_prepare_bars_sets_gateway(self) -> None:
        raw = BarData(
            symbol="INTCUSDT_SWAP_BINANCE",
            exchange=Exchange.GLOBAL,
            datetime=datetime(2026, 7, 3, 13, 30, tzinfo=timezone.utc),
            interval=Interval.MINUTE,
            open_price=1.0,
            high_price=2.0,
            low_price=0.5,
            close_price=1.5,
            volume=1.0,
            gateway_name="BACKTESTING",
        )
        out = _prepare_bars([raw])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].gateway_name, "BACKTESTING")
        self.assertEqual(_bar_ms(out[0].datetime), _bar_ms(raw.datetime))

    def test_session_bounds_early_close_jul3(self) -> None:
        cfg = OrbConfig.from_env()
        _, end, _, close_ms, close_et = session_bounds_for_date("2026-07-03", cfg)
        self.assertEqual(close_et, "13:00")
        et = pd.Timestamp(close_ms, unit="ms", tz="America/New_York")
        self.assertEqual(et.strftime("%H:%M"), "13:00")

    def test_fetch_start_for_load_bar(self) -> None:
        from tools.cta.backtest_kk_vnpy import _fetch_start_for_load_bar

        cfg = OrbConfig.from_env()
        engine_start, fetch_from = _fetch_start_for_load_bar("2026-07-03", "2026-06-25", cfg)
        self.assertLessEqual(fetch_from, engine_start)
        self.assertLess(fetch_from, "2026-06-21")


class TestKkVnpyBacktestStrategy(unittest.TestCase):
    def test_compound_wallet_updates_on_close(self) -> None:
        from orb.kk.vnpy.strategies.king_keltner_kk_backtest import KingKeltnerKkBacktestStrategy

        strat = KingKeltnerKkBacktestStrategy.__new__(KingKeltnerKkBacktestStrategy)
        strat.kk_bt_wallet = 14.0
        strat.fixed_size = 0.1
        strat.pos = 0
        strat._bt_entry_px = 100.0
        strat._bt_entry_side = 1
        strat.vt_symbol = kk_vt_symbol("INTCUSDT")

        class _T:
            price = 101.0
            volume = 0.1

        strat._apply_close_pnl(_T())
        self.assertGreater(strat.kk_bt_wallet, 14.0)


if __name__ == "__main__":
    unittest.main()
