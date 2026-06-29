from __future__ import annotations

import pandas as pd

from orb.core.config import OrbConfig
from orb.core.rth_daily import aggregate_rth_daily_bars


def test_aggregate_rth_daily_bars_one_session():
    cfg = OrbConfig(
        session_tz="America/New_York",
        session_open_time="09:30",
        session_close_time="16:00",
        signal_interval="5m",
    )
    day = pd.Timestamp("2026-03-01 09:30", tz="America/New_York")
    rows = []
    for i in range(78):
        ts = day + pd.Timedelta(minutes=5 * i)
        rows.append(
            {
                "open_time": int(ts.value // 1_000_000),
                "open": 100.0 + i * 0.01,
                "high": 101.0 + i * 0.01,
                "low": 99.0 + i * 0.01,
                "close": 100.5 + i * 0.01,
                "volume": 1000.0,
            }
        )
    df5 = pd.DataFrame(rows)
    daily = aggregate_rth_daily_bars(df5, cfg)
    assert len(daily) == 1
    assert int(daily["open_time"].iloc[0]) == int(day.value // 1_000_000)
    assert float(daily["open"].iloc[0]) == 100.0
    assert float(daily["close"].iloc[0]) == round(100.5 + 77 * 0.01, 8)
