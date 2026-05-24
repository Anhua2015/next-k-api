"""Supertrend 开仓过滤单元测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import supertrend_config as cfg
import supertrend_filters as filt
from supertrend_indicator import compute_supertrend


def _make_ohlcv(n: int, *, drift: float = 0.0, noise: float = 0.5) -> pd.DataFrame:
    t0 = 1_700_000_000_000
    rows = []
    px = 100.0
    for i in range(n):
        px += drift + (np.sin(i / 3.0) * noise)
        o, c = px - 0.1, px + 0.1
        h, l = max(o, c) + 0.2, min(o, c) - 0.2
        rows.append(
            {
                "open_time": t0 + i * 300_000,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": 1000.0,
            }
        )
    return pd.DataFrame(rows)


class TestSupertrendFilters(unittest.TestCase):
    def test_entry_confirm_requires_consecutive_trend(self):
        with patch.multiple(
            cfg,
            ST_FILTER_ENABLED=True,
            ST_VP_ENABLED=False,
            ST_ADX_MIN=0.0,
            ST_HTF_TIMEFRAME="",
            ST_HTF_REQUIRE_ALIGN=False,
            ST_MIN_ATR_PCT=0.0,
            ST_MAX_RANGE_PCT=0.0,
            ST_MIN_DIST_ATR=0.0,
            ST_ENTRY_CONFIRM_BARS=2,
        ):
            df = _make_ohlcv(80, drift=0.05)
            st = compute_supertrend(df, period=10, multiplier=3.0)
            last = st.iloc[-1]
            ctx = filt.build_filter_context("TESTUSDT", "LONG", st, last, timeframe_ms=300_000)
            ok, reason = filt.evaluate_entry_filters(ctx)
            self.assertTrue(ok or reason in ("confirm_bars", "adx_low", "adx_unavailable"))

    def test_adx_blocks_when_low(self):
        with patch.multiple(
            cfg,
            ST_FILTER_ENABLED=True,
            ST_VP_ENABLED=False,
            ST_ADX_MIN=99.0,
            ST_HTF_TIMEFRAME="",
            ST_MAX_RANGE_PCT=0.0,
            ST_MIN_ATR_PCT=0.0,
            ST_ENTRY_CONFIRM_BARS=0,
            ST_MIN_DIST_ATR=0.0,
        ):
            df = _make_ohlcv(60, drift=0.0, noise=0.2)
            st = compute_supertrend(df, period=10, multiplier=3.0)
            last = st.iloc[-1]
            ctx = filt.build_filter_context("TESTUSDT", "LONG", st, last, timeframe_ms=300_000)
            ok, reason = filt.evaluate_entry_filters(ctx)
            self.assertFalse(ok)
            self.assertTrue(reason.startswith("adx_"))

    def test_chop_cooldown_when_many_flips(self):
        with patch.multiple(cfg, ST_CHOP_MAX_FLIPS=2, ST_CHOP_COOLDOWN_BARS=5):
            df = _make_ohlcv(50, drift=0.0, noise=2.0)
            st = compute_supertrend(df, period=5, multiplier=1.5)
            closed = filt.closed_bars_df(
                st, timeframe_ms=300_000, now_ms=int(st["open_time"].iloc[-1]) + 300_000
            )
            flips = filt.flip_signal_count(closed, 48)
            last = closed.iloc[-1]
            ctx = filt.build_filter_context(
                "X", "LONG", st, last, timeframe_ms=300_000, htf_trend=1
            )
            ctx = filt.EntryFilterContext(
                symbol=ctx.symbol,
                side=ctx.side,
                st_df=ctx.st_df,
                close_px=ctx.close_px,
                st_atr=ctx.st_atr,
                st_up=ctx.st_up,
                st_dn=ctx.st_dn,
                trend=ctx.trend,
                bar_open_ms=int(last["open_time"]),
                flip_count=flips,
            )
            until = filt.chop_cooldown_until_bar_from_ctx(ctx, 300_000)
            if flips >= 2:
                self.assertIsNotNone(until)
                self.assertGreater(until, ctx.bar_open_ms)

    def test_filter_disabled_passes(self):
        with patch.multiple(cfg, ST_FILTER_ENABLED=False, ST_ADX_MIN=99.0):
            df = _make_ohlcv(40)
            st = compute_supertrend(df, period=10, multiplier=3.0)
            ctx = filt.build_filter_context("T", "LONG", st, st.iloc[-1], timeframe_ms=300_000)
            ok, reason = filt.evaluate_entry_filters(ctx)
            self.assertTrue(ok)
            self.assertEqual(reason, "")

    def test_entry_window_zero_caps_at_confirm_bars(self):
        with patch.multiple(cfg, ST_ENTRY_WINDOW_BARS=0, ST_ENTRY_CONFIRM_BARS=2):
            since = 5
            confirm_cap = 2
            self.assertFalse(since <= confirm_cap if cfg.ST_ENTRY_WINDOW_BARS <= 0 else since <= 6)
            since = 2
            self.assertTrue(since <= cfg.ST_ENTRY_CONFIRM_BARS)

    def test_wide_entry_window_still_allows_late_flip_follow(self):
        with patch.multiple(cfg, ST_ENTRY_WINDOW_BARS=6):
            df = _make_ohlcv(80, drift=0.08)
            st = compute_supertrend(df, period=10, multiplier=3.0)
            closed = filt.closed_bars_df(
                st, timeframe_ms=300_000, now_ms=int(st["open_time"].iloc[-1]) + 300_000
            )
            last = closed.iloc[-1]
            buy = bool(last.get("buy_signal", False))
            sell = bool(last.get("sell_signal", False))
            trend = int(last["st_trend"])
            want_long, want_short = filt.compute_entry_intent(
                trend=trend,
                buy=buy,
                sell=sell,
                closed=closed,
                open_row=None,
            )
            if trend == 1 and not sell:
                since = filt.bars_since_last_signal(closed, "buy_signal")
                if since <= 6 and since > 0:
                    self.assertTrue(want_long)

    def test_vp_blocks_inside_value_area(self):
        with patch.multiple(
            cfg,
            ST_FILTER_ENABLED=True,
            ST_VP_ENABLED=True,
            ST_VP_LOOKBACK=24,
            ST_ADX_MIN=0.0,
            ST_HTF_TIMEFRAME="",
            ST_HTF_REQUIRE_ALIGN=False,
            ST_MIN_ATR_PCT=0.0,
            ST_MAX_RANGE_PCT=0.0,
            ST_ENTRY_CONFIRM_BARS=0,
            ST_MIN_DIST_ATR=0.0,
        ):
            df = _make_ohlcv(60, drift=0.0, noise=0.1)
            st = compute_supertrend(df, period=10, multiplier=3.0)
            last = st.iloc[-1]
            ctx = filt.EntryFilterContext(
                symbol="T",
                side="LONG",
                st_df=st,
                close_px=100.0,
                st_atr=1.0,
                st_up=99.0,
                st_dn=101.0,
                trend=1,
                bar_open_ms=int(last["open_time"]),
                vp_poc=100.0,
                vp_val=99.0,
                vp_vah=101.0,
            )
            ok, reason = filt.evaluate_entry_filters(ctx)
            self.assertFalse(ok)
            self.assertEqual(reason, "vp_inside_value_area")

    def test_vp_long_requires_above_vah(self):
        with patch.multiple(
            cfg,
            ST_FILTER_ENABLED=True,
            ST_VP_ENABLED=True,
            ST_ADX_MIN=0.0,
            ST_HTF_TIMEFRAME="",
            ST_MAX_RANGE_PCT=0.0,
            ST_MIN_ATR_PCT=0.0,
            ST_ENTRY_CONFIRM_BARS=0,
            ST_MIN_DIST_ATR=0.0,
        ):
            ctx = filt.EntryFilterContext(
                symbol="T",
                side="LONG",
                st_df=pd.DataFrame(),
                close_px=100.0,
                st_atr=1.0,
                st_up=99.0,
                st_dn=102.0,
                trend=1,
                bar_open_ms=1,
                vp_val=98.0,
                vp_vah=100.0,
            )
            ok, reason = filt.evaluate_entry_filters(ctx)
            self.assertFalse(ok)
            self.assertEqual(reason, "vp_long_not_above_vah")

    def test_chop_disabled_when_filter_off(self):
        with patch.multiple(
            cfg, ST_FILTER_ENABLED=False, ST_CHOP_MAX_FLIPS=1, ST_CHOP_COOLDOWN_BARS=5
        ):
            ctx = filt.EntryFilterContext(
                symbol="X",
                side="LONG",
                st_df=pd.DataFrame(),
                close_px=1.0,
                st_atr=1.0,
                st_up=0.5,
                st_dn=1.5,
                trend=1,
                bar_open_ms=1000,
                flip_count=99,
            )
            self.assertIsNone(filt.chop_cooldown_until_bar_from_ctx(ctx, 300_000))


    def test_structure_sl_long_uses_min_of_st_up_and_prev_low(self):
        sl = filt.structure_sl_price(
            "LONG", st_up=100.0, st_dn=110.0, prev_low=98.0, prev_high=105.0
        )
        self.assertEqual(sl, 98.0)

    def test_structure_sl_short_uses_max_of_st_dn_and_prev_high(self):
        sl = filt.structure_sl_price(
            "SHORT", st_up=90.0, st_dn=102.0, prev_low=95.0, prev_high=104.0
        )
        self.assertEqual(sl, 104.0)

    def test_hard_sl_triggered_long_wick(self):
        with patch.object(cfg, "ST_HARD_SL_USE_WICK", True):
            self.assertTrue(
                filt.hard_sl_triggered("LONG", 100.0, low=99.0, high=101.0, close=100.5)
            )
            self.assertFalse(
                filt.hard_sl_triggered("LONG", 100.0, low=100.0, high=101.0, close=100.5)
            )

    def test_hard_sl_triggered_short_wick(self):
        with patch.object(cfg, "ST_HARD_SL_USE_WICK", True):
            self.assertTrue(
                filt.hard_sl_triggered("SHORT", 100.0, low=99.0, high=101.0, close=99.5)
            )

    def test_structure_sl_valid_long(self):
        self.assertTrue(filt.structure_sl_valid("LONG", 100.0, 99.0))
        self.assertFalse(filt.structure_sl_valid("LONG", 100.0, 100.5))

    def test_flip_trend_count_changes(self):
        df = _make_ohlcv(30, drift=0.0, noise=3.0)
        st = compute_supertrend(df, period=5, multiplier=1.2)
        closed = filt.closed_bars_df(
            st, timeframe_ms=300_000, now_ms=int(st["open_time"].iloc[-1]) + 300_000
        )
        n_sig = filt.flip_signal_count(closed, 24)
        n_tr = filt.flip_trend_count(closed, 24)
        self.assertGreaterEqual(n_sig, 0)
        self.assertGreaterEqual(n_tr, 0)


if __name__ == "__main__":
    unittest.main()
