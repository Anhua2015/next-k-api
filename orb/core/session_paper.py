"""RTH / 会话辅助（vnpy lane 共用，不含 ORB V2 纸面扫描）。"""

from __future__ import annotations

import time
from typing import Optional

import pandas as pd

from binance_fapi import fetch_klines_forward, klines_to_df
from orb.core.config import OrbConfig
from orb.core.session import extended_fetch_anchor_ms, is_trading_session, session_day_str


def _session_date_now(cfg: OrbConfig) -> str:
    return session_day_str(
        int(time.time() * 1000), tz=cfg.session_tz, session_open_time=cfg.session_open_time
    )


def _drop_forming_bar(df: pd.DataFrame, cfg: OrbConfig, *, now_ms: Optional[int] = None) -> pd.DataFrame:
    if df.empty:
        return df
    t = int(now_ms if now_ms is not None else time.time() * 1000)
    step = cfg.bar_step_ms()
    last_open = int(df["open_time"].iloc[-1])
    if last_open + step > t:
        return df.iloc[:-1].reset_index(drop=True)
    return df


def _load_1m_df(symbol: str, cfg: OrbConfig, *, now_ms: Optional[int] = None) -> pd.DataFrame:
    end_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    day0 = extended_fetch_anchor_ms(end_ms, cfg)
    rows = fetch_klines_forward(symbol, "1m", day0, end_ms)
    df = klines_to_df(rows)
    if df.empty:
        return df
    df = df.drop_duplicates(subset=["open_time"], keep="last").sort_values("open_time").reset_index(drop=True)
    return _drop_forming_bar(df, cfg, now_ms=end_ms)


def in_regular_session(cfg: OrbConfig, *, now_ms: Optional[int] = None) -> bool:
    if not (cfg.session_open_time or "").strip():
        return True
    t = int(now_ms if now_ms is not None else time.time() * 1000)
    return is_trading_session(
        t,
        tz=cfg.session_tz,
        session_open_time=cfg.session_open_time,
        session_close_time=cfg.session_close_time,
        market=cfg.market,
    )
