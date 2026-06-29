from __future__ import annotations

import pandas as pd

from orb.core.ema import aggregate_ohlcv, ema_trend_allows, ema_values_asof


def test_ema_trend_allows():
    assert ema_trend_allows("LONG", 10.0, 9.0)
    assert not ema_trend_allows("LONG", 9.0, 10.0)
    assert ema_trend_allows("SHORT", 9.0, 10.0)


def test_aggregate_and_ema_asof():
    rows = []
    for i in range(30):
        rows.append(
            {
                "open_time": 1_700_000_000_000 + i * 900_000,
                "open": 100 + i,
                "high": 101 + i,
                "low": 99 + i,
                "close": 100 + i,
                "volume": 1.0,
            }
        )
    df = pd.DataFrame(rows)
    agg = aggregate_ohlcv(df, 900_000)
    asof = int(agg["open_time"].iloc[-1])
    v = ema_values_asof(agg, asof)
    assert v is not None
