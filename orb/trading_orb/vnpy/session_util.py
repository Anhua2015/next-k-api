"""Trading ORB vnpy 会话辅助（supervisor 等待下一交易日）。"""

from __future__ import annotations

import time

import pandas as pd

from orb.core.config import OrbConfig
from orb.core.session import session_day_str


def seconds_until_next_session_open(
    cfg: OrbConfig,
    *,
    now_ms: int | None = None,
    buffer_minutes: int = 5,
) -> float:
    """距下一 session_open 的秒数（至少 60s）。"""
    ms = int(now_ms if now_ms is not None else time.time() * 1000)
    tz = cfg.session_tz
    open_time = (cfg.session_open_time or "09:30").strip()
    day = session_day_str(ms, tz=tz, session_open_time=open_time)
    ts = pd.Timestamp(ms, unit="ms", tz=tz)
    anchor_today = pd.Timestamp(f"{day} {open_time}", tz=tz)
    if ts < anchor_today:
        target = anchor_today
    else:
        target = anchor_today + pd.Timedelta(days=1)
    target = target - pd.Timedelta(minutes=int(buffer_minutes))
    wait = float(target.timestamp() - ts.timestamp())
    return max(60.0, wait)
