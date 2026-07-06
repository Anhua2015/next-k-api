"""日级时间序列动量（TSMOM 压缩版），供 ORB 方向过滤。"""

from __future__ import annotations

from typing import Optional, Tuple

import pandas as pd


def daily_momentum_asof(
    daily_df: pd.DataFrame,
    asof_open_ms: int,
    *,
    lookback_days: int = 5,
    tz: str = "America/New_York",
) -> Tuple[Optional[int], Optional[float]]:
    """截至 asof 当日开盘前，用已完成日线计算 N 日动量方向。

    Returns:
        (direction, ret) where direction is +1 up, -1 down, 0 flat; ret is decimal return.
        (None, None) if insufficient history.
    """
    days = max(1, int(lookback_days))
    if daily_df.empty:
        return None, None
    df = daily_df.drop_duplicates(subset=["open_time"], keep="last").sort_values("open_time")
    tz_name = tz or "UTC"
    asof_day = pd.Timestamp(int(asof_open_ms), unit="ms", tz=tz_name).normalize()
    day_ts = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_convert(tz_name).dt.normalize()
    completed = df[day_ts < asof_day]
    if len(completed) < days + 1:
        return None, None
    closes = completed["close"].astype(float)
    ref = float(closes.iloc[-1])
    base = float(closes.iloc[-1 - days])
    if ref <= 0 or base <= 0:
        return None, None
    ret = ref / base - 1.0
    if ret > 0:
        return 1, ret
    if ret < 0:
        return -1, ret
    return 0, ret
