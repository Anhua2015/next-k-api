"""每标机器人权益（复利读 kk_symbol_bots）。"""

from __future__ import annotations

import sqlite3
from typing import Optional

from orb.core.kline_cache import norm_symbol
from orb.kk.config import KKConfig
from orb.kk.db import load_wallet


def symbol_equity_usdt(
    kk: KKConfig,
    symbol: str,
    *,
    cur: Optional[sqlite3.Cursor] = None,
) -> float:
    """单机器人权益；compound 且提供 cursor 时读 kk_symbol_bots。"""
    base = float(kk.equity_usdt or 14.0)
    if not kk.compound or cur is None:
        return base
    return load_wallet(cur, norm_symbol(symbol), default=base)
