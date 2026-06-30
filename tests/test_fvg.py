"""FVG prox entry tests."""

from __future__ import annotations

import unittest

import pandas as pd

from orb.core.config import OrbConfig
from orb.core.fvg import (
    FvgZone,
    find_fvg_limit_entry,
    find_limit_fill,
    prox_entry_for_zone,
    scan_first_fvg,
    stop_loss_for_fvg_fill,
    synthesize_fvg_fill_from_protocol,
)
from orb.core.signals import OrbSignal


class TestFvg(unittest.TestCase):
    def test_scan_short_fvg(self):
        df1 = pd.DataFrame(
            {
                "open_time": [1000, 1060, 1120, 1180],
                "open": [160.0, 159.0, 155.0, 154.0],
                "high": [160.5, 159.5, 155.5, 154.5],
                "low": [159.8, 155.62, 154.92, 153.0],
                "close": [159.9, 155.0, 154.99, 153.5],
                "volume": [1, 1, 1, 1],
            }
        )
        zone = scan_first_fvg(
            df1,
            side="SHORT",
            after_ms=1050,
            before_ms=2000,
            or_end_ms=1055,
            min_gap_pct=0.01,
        )
        self.assertIsNotNone(zone)
        assert zone is not None
        self.assertEqual(zone.side, "SHORT")
        self.assertAlmostEqual(zone.high, 155.62)
        self.assertAlmostEqual(zone.low, 154.5)

    def test_prox_entry_long(self):
        z = FvgZone(side="LONG", low=198.0, high=199.5, form_bar_open_ms=1120)
        self.assertAlmostEqual(prox_entry_for_zone(z), 199.5)

    def test_prox_entry_short(self):
        z = FvgZone(side="SHORT", low=154.99, high=155.62, form_bar_open_ms=1120)
        self.assertAlmostEqual(prox_entry_for_zone(z), 154.99)

    def test_limit_fill_short(self):
        df1 = pd.DataFrame(
            {
                "open_time": [1200, 1260],
                "high": [155.20, 156.00],
                "low": [154.50, 155.80],
            }
        )
        hit = find_limit_fill(df1, side="SHORT", entry_px=154.99, after_ms=1100, before_ms=2000)
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertAlmostEqual(hit[1], 154.99)

    def test_sl_from_prox_fill_long_atr(self):
        """LONG 近沿成交：止损 = 近沿价 - ATR×fraction。"""
        cfg = OrbConfig(sl_mode="atr_pct", atr_sl_fraction=0.05, exit_mode="eod")
        sig = OrbSignal(
            "TSLAUSDT",
            200.0,
            "LONG",
            "ORB_BREAKOUT_LONG",
            "high",
            or_high=201.0,
            or_low=195.0,
            sl_price=199.5,
            r_unit=0.5,
        )
        fill_px = 198.0
        sl = stop_loss_for_fvg_fill(
            side="LONG", fill_px=fill_px, sig=sig, cfg=cfg, daily_atr=10.0
        )
        self.assertAlmostEqual(sl, 197.5)

    def test_sl_from_prox_fill_short_atr(self):
        """SHORT 近沿成交：止损 = 近沿价 + ATR×fraction。"""
        cfg = OrbConfig(sl_mode="atr_pct", atr_sl_fraction=0.05, exit_mode="eod")
        sig = OrbSignal(
            "TSLAUSDT",
            190.0,
            "SHORT",
            "ORB_BREAKOUT_SHORT",
            "high",
            or_high=201.0,
            or_low=195.0,
            sl_price=190.5,
            r_unit=0.5,
        )
        fill_px = 192.0
        sl = stop_loss_for_fvg_fill(
            side="SHORT", fill_px=fill_px, sig=sig, cfg=cfg, daily_atr=10.0
        )
        self.assertAlmostEqual(sl, 192.5)

    def test_fvg_no_infinite_loop_on_or_reclaim(self):
        """OR 回收后 cursor 前进，不应反复命中同一 reclaim 卡死。"""
        cfg = OrbConfig(or_minutes=15, session_tz="America/New_York", session_open_time="09:30", session_close_time="16:00")
        # 5m bars: 09:45 confirm close, 10:00 OR reclaim inside range
        df5 = pd.DataFrame(
            {
                "open_time": [3_600_000 * 1000 + i * 300_000 for i in range(20)],
                "open": [100.0] * 20,
                "high": [101.0] * 20,
                "low": [99.0] * 20,
                "close": [100.5] * 20,
            }
        )
        # reclaim at bar index 3 (10:00): close back inside OR
        df5.loc[3, "close"] = 100.0
        df5.loc[3, "low"] = 99.5
        df1 = pd.DataFrame(
            {
                "open_time": [3_600_000 * 1000 + i * 60_000 for i in range(120)],
                "open": [100.0] * 120,
                "high": [100.1] * 120,
                "low": [99.9] * 120,
                "close": [100.0] * 120,
                "volume": [1] * 120,
            }
        )
        entry_bo = df5["open_time"].iloc[2]
        sig = OrbSignal(
            "TSLAUSDT",
            101.0,
            "LONG",
            "ORB_BREAKOUT_LONG",
            "high",
            or_high=100.8,
            or_low=99.5,
            entry_bar_open_ms=int(entry_bo),
            sl_price=99.0,
            r_unit=1.0,
        )
        import time

        t0 = time.time()
        fill_sig, reason, _ = find_fvg_limit_entry(
            sig,
            df1,
            df5,
            scan_ms=int(entry_bo),
            close_ms=int(df5["open_time"].iloc[-1]) + 300_000,
            bar=300_000,
            cfg=cfg,
            asof_ms=None,
        )
        elapsed = time.time() - t0
        self.assertLess(elapsed, 2.0, "find_fvg_limit_entry hung (reclaim loop?)")
        self.assertIn(
            reason,
            ("fvg_not_found", "fvg_limit_not_filled", "ok", "fvg_pending", "fvg_limit_pending"),
        )


    def test_synthesize_fvg_fill_from_protocol(self):
        cfg = OrbConfig(sl_mode="or_range", exit_mode="eod")
        sig = OrbSignal(
            "TSLAUSDT",
            101.0,
            "LONG",
            "ORB_BREAKOUT_LONG",
            "high",
            or_high=100.8,
            or_low=99.5,
            entry_bar_open_ms=900_000,
            sl_price=99.0,
            r_unit=1.0,
        )
        quote = OrbSignal(
            "TSLAUSDT",
            100.5,
            "LONG",
            "ORB_BREAKOUT_LONG",
            "high",
            or_high=100.8,
            or_low=99.5,
            sl_price=99.5,
            tp_price=102.0,
            entry_bar_open_ms=900_000,
            r_unit=1.0,
        )
        fill = synthesize_fvg_fill_from_protocol(
            sig,
            cfg,
            now_ms=1_200_000,
            quote=quote,
            protocol_entry_px=100.48,
        )
        self.assertIsNotNone(fill)
        assert fill is not None
        self.assertAlmostEqual(float(fill.price), 100.48)
        self.assertEqual(fill.fvg_confirm_bar_ms, 900_000)
        self.assertAlmostEqual(float(fill.sl_price or 0), 99.5)


if __name__ == "__main__":
    unittest.main()
