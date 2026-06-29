from __future__ import annotations

import pandas as pd

from orb.core.config import OrbConfig
from orb.core.paper import resolve_daily_atr, uses_atr_sl
from tools.orb.v2.backtest_universe import filter_backtest_sessions_with_atr, session_date_has_atr


def test_uses_atr_sl():
    assert uses_atr_sl(OrbConfig(sl_mode="atr_pct"))
    assert not uses_atr_sl(OrbConfig(sl_mode="or_range"))


def test_resolve_daily_atr_insufficient():
    cfg = OrbConfig(sl_mode="atr_pct", atr_period=14, session_tz="UTC", session_open_time="")
    tz = "UTC"
    rows = []
    for i in range(10):
        ts = pd.Timestamp("2024-01-01", tz=tz) + pd.Timedelta(days=i)
        rows.append(
            {
                "open_time": int(ts.value // 1_000_000),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1.0,
            }
        )
    df = pd.DataFrame(rows)
    asof = int(rows[-1]["open_time"])
    assert resolve_daily_atr(df, asof_open_ms=asof, now_ms=asof, cfg=cfg) is None


def test_filter_backtest_sessions_with_atr_or_range_passthrough():
    cfg = OrbConfig(sl_mode="or_range")
    dates = ["2026-01-01", "2026-01-02"]
    assert filter_backtest_sessions_with_atr(dates, ["COINUSDT"], cfg) == dates


def test_session_date_has_atr_coin_early_session():
    """COIN 缓存早期 session 无足够 1d 样本时应为 False（若本地有缓存）。"""
    from orb.core.kline_cache import has_kline_cache

    if not has_kline_cache("COINUSDT", "5m"):
        return
    cfg = OrbConfig.from_env()
    cfg.sl_mode = "atr_pct"
    # 样本不足的前几个交易日
    assert session_date_has_atr("COINUSDT", "2026-02-09", cfg) is False
    assert session_date_has_atr("COINUSDT", "2026-02-23", cfg) is True
