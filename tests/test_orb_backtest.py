"""ORB 回测结算边界测试。"""

from __future__ import annotations

import unittest

import pandas as pd

from orb.config import OrbConfig
from orb.resolve import resolve_forward


def _1m_bars(day: str, start_hm: str, end_hm: str, *, close: float = 100.1) -> pd.DataFrame:
    tz = "America/New_York"
    t0 = pd.Timestamp(f"{day} {start_hm}", tz=tz)
    t1 = pd.Timestamp(f"{day} {end_hm}", tz=tz)
    rows = []
    t = t0
    while t <= t1:
        rows.append(
            {
                "open_time": int(t.value // 1_000_000),
                "open": close,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "volume": 1.0,
            }
        )
        t += pd.Timedelta(minutes=1)
    return pd.DataFrame(rows)


class TestOrbBacktestResolve(unittest.TestCase):
    def test_eod_needs_1m_hist_end_not_5m_cutoff(self):
        """5m 最后一根 15:55 时，应用 1m 数据到 15:59 才能触发 16:00 收盘平仓。"""
        tz = "America/New_York"
        entry_bo = int(pd.Timestamp("2024-06-03 10:00", tz=tz).value // 1_000_000)
        hist_end_5m = int(pd.Timestamp("2024-06-03 15:55", tz=tz).value // 1_000_000)
        df_1m = _1m_bars("2024-06-03", "10:01", "15:59", close=101.0)
        cfg = OrbConfig(
            session_tz=tz,
            session_open_time="09:30",
            session_close_time="16:00",
            resolve_at_session_close=True,
            exit_mode="eod",
            early_exit_minutes=0,
            signal_interval="5m",
        )
        out_short, _, note_short, _, _ = resolve_forward(
            df_1m,
            entry=100.0,
            entry_bar_open_ms=entry_bo,
            side="LONG",
            sl=99.0,
            tp=None,
            hist_end_ms=hist_end_5m,
            bar_step_ms=cfg.bar_step_ms(),
            cfg=cfg,
        )
        out_long, px, note, _, _ = resolve_forward(
            df_1m,
            entry=100.0,
            entry_bar_open_ms=entry_bo,
            side="LONG",
            sl=99.0,
            tp=None,
            hist_end_ms=int(df_1m["open_time"].iloc[-1]),
            bar_step_ms=cfg.bar_step_ms(),
            cfg=cfg,
        )
        self.assertIsNone(out_short)
        self.assertEqual(out_long, "session_close")
        self.assertEqual(note, "resolved:session_close")
        self.assertAlmostEqual(px, 101.0)


if __name__ == "__main__":
    unittest.main()
