"""GTL engine + vnpy strategy smoke tests."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from orb.gtl.engine import GtlEngine, _display_prob, compute_gtl_dataframe
from orb.gtl.vnpy.strategy import GtlBreakoutStrategy


def _synthetic_df(n: int = 800) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    px = 100.0
    rows = []
    t0 = 1_700_000_000_000
    for i in range(n):
        px *= 1.0 + float(rng.normal(0, 0.002))
        h = px * (1 + abs(rng.normal(0, 0.001)))
        l = px * (1 - abs(rng.normal(0, 0.001)))
        o = px * (1 + float(rng.normal(0, 0.0005)))
        rows.append({"open_time": t0 + i * 60_000, "open": o, "high": h, "low": l, "close": px, "volume": 1.0})
    return pd.DataFrame(rows)


class TestGtlEngine(unittest.TestCase):
    def test_streaming_produces_angles(self) -> None:
        df = _synthetic_df(600)
        eng = GtlEngine(lookback=23, vol_window=100)
        last = None
        for _, row in df.iterrows():
            last = eng.update(row["open"], row["high"], row["low"], row["close"])
        self.assertIsNotNone(last)
        assert last is not None
        self.assertNotEqual(last.frozen_hh, 0.0)
        self.assertNotEqual(last.frozen_ll, 0.0)

    def test_batch_matches_streaming(self) -> None:
        df = _synthetic_df(400)
        batch = compute_gtl_dataframe(df, lookback=23, vol_window=100)
        eng = GtlEngine(lookback=23, vol_window=100)
        last = None
        for _, row in df.iterrows():
            last = eng.update(row["open"], row["high"], row["low"], row["close"])
        assert last is not None
        self.assertAlmostEqual(batch.iloc[-1]["theta_ceiling"], last.theta_ceiling, places=4)
        self.assertAlmostEqual(batch.iloc[-1]["frozen_hh"], last.frozen_hh, places=4)

    def test_break_bar_exposes_broken_box(self) -> None:
        df = _synthetic_df(1000)
        eng = GtlEngine(lookback=23, vol_window=100)
        broken = None
        for _, row in df.iterrows():
            r = eng.update(row["open"], row["high"], row["low"], row["close"])
            if r.break_dir:
                broken = r
        self.assertIsNotNone(broken)
        assert broken is not None
        self.assertGreater(broken.broken_hh, broken.broken_ll)

    def test_birth_break_alignment(self) -> None:
        df = _synthetic_df(1200)
        out = compute_gtl_dataframe(df, lookback=23, vol_window=100)
        aligned = out[out["break_aligns_birth"]]
        breaks = out[out["break_dir"] != 0]
        self.assertGreater(len(breaks), 0)
        # aligned breaks must be subset of gated birth breaks
        self.assertTrue((aligned.index.isin(breaks.index)).all())
        if len(aligned):
            row = aligned.iloc[0]
            if row["break_dir"] > 0:
                self.assertTrue(row["birth_signal_up"])
            else:
                self.assertTrue(row["birth_signal_down"])

    def test_display_prob_shrinkage_low_neff(self) -> None:
        # n_eff=16, prob_up=0.979 -> display ~ 75.5% (TV-style)
        disp = _display_prob(0.979, 16.0, 0.979, cal_total=0.0)
        self.assertAlmostEqual(disp, 0.755, places=2)

    def test_forecast_columns_present(self) -> None:
        df = _synthetic_df(800)
        out = compute_gtl_dataframe(df, lookback=23, vol_window=100)
        self.assertIn("display_prob_up", out.columns)
        self.assertIn("forecast_up", out.columns)
        self.assertIn("theta_ceiling_display", out.columns)
        last = out.iloc[-1]
        self.assertGreaterEqual(float(last["theta_ceiling_display"]), 0.0)


class TestGtlVnpyStrategy(unittest.TestCase):
    def test_strategy_class_parameters(self) -> None:
        self.assertIn("lookback", GtlBreakoutStrategy.parameters)
        self.assertIn("trade_mode", GtlBreakoutStrategy.parameters)


if __name__ == "__main__":
    unittest.main()
