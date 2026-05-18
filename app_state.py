"""进程内共享状态（交易所连接等）。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional


class AppState:
    ccxt_exchange = None
    yfinance_available = False
    startup_time: Optional[datetime] = None


state = AppState()
