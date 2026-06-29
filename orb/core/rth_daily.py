"""RTH session 日线：由 5m K 线按 session_open/close 聚合。"""

from __future__ import annotations

from typing import List

import pandas as pd

from orb.core.config import OrbConfig
from orb.core.session import session_anchor_ms, session_close_ms, session_day_str


def aggregate_rth_daily_bars(df5: pd.DataFrame, cfg: OrbConfig) -> pd.DataFrame:
    """将 5m K 线聚合为 RTH session 日线（open_time = 当日 session 起点 ms）。"""
    cols = ["open_time", "open", "high", "low", "close", "volume"]
    if df5.empty:
        return pd.DataFrame(columns=cols)
    tz = cfg.session_tz
    open_time = cfg.session_open_time
    close_time = cfg.session_close_time
    df = (
        df5.drop_duplicates(subset=["open_time"], keep="last")
        .sort_values("open_time")
        .reset_index(drop=True)
    )
    session_dates: List[str] = []
    seen = set()
    for open_ms in df["open_time"]:
        day = session_day_str(int(open_ms), tz=tz, session_open_time=open_time)
        if day not in seen:
            seen.add(day)
            session_dates.append(day)
    rows: List[dict] = []
    for day in session_dates:
        ts = pd.Timestamp(f"{day} 12:00:00", tz=tz)
        probe_ms = int(ts.value // 1_000_000)
        anchor = session_anchor_ms(probe_ms, tz=tz, session_open_time=open_time)
        close_ms = session_close_ms(anchor, tz=tz, session_close_time=close_time)
        if close_ms is None:
            close_ms = anchor + 390 * 60_000
        sess = df[(df["open_time"] >= anchor) & (df["open_time"] < close_ms)]
        if sess.empty:
            continue
        rows.append(
            {
                "open_time": int(anchor),
                "open": float(sess["open"].iloc[0]),
                "high": float(sess["high"].max()),
                "low": float(sess["low"].min()),
                "close": float(sess["close"].iloc[-1]),
                "volume": float(sess["volume"].sum()),
            }
        )
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows).sort_values("open_time").reset_index(drop=True)
