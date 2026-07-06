"""research_vnpy_cta --legacy 用的 session 切片与旧引擎入口。"""
from __future__ import annotations

import pandas as pd

from orb.core.config import OrbConfig
from orb.core.session import session_anchor_ms, session_close_ms


def _session_slice(df: pd.DataFrame, session_date: str, cfg: OrbConfig) -> pd.DataFrame:
    tz = cfg.session_tz
    ts = pd.Timestamp(f"{session_date} 12:00:00", tz=tz)
    anchor = session_anchor_ms(int(ts.value // 1_000_000), tz=tz, session_open_time=cfg.session_open_time)
    close = session_close_ms(anchor, tz=tz, session_close_time=cfg.session_close_time)
    if close is None:
        close = anchor + 6 * 60 * 60 * 1000
    return df[(df["open_time"] >= anchor) & (df["open_time"] <= close)].copy()
