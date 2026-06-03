"""组合层风控：同向敞口上限、与 BTC 高相关时减仓。"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from moss_quant import config as cfg

logger = logging.getLogger(__name__)

_BTC_SYMBOL = "BTCUSDT"
_CORR_CACHE: Dict[str, Tuple[float, float]] = {}


def _wallet_budget_usdt(conn: sqlite3.Connection) -> float:
    from moss_quant.db import aggregate_moss_wallet_initial

    try:
        return float(aggregate_moss_wallet_initial(conn) or 0)
    except Exception:
        return float(cfg.MOSS_QUANT_PROFILE_CAPITAL) * max(
            1, int(cfg.MOSS_QUANT_MAX_ACTIVE_PROFILES)
        )


def list_open_signal_exposure(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT profile_id, symbol, side,
                  COALESCE(virtual_notional_usdt, 0) AS notional
           FROM moss_signals
           WHERE outcome IS NULL AND side IN ('LONG', 'SHORT')"""
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        sym = str(r["symbol"] or "").upper()
        side = str(r["side"] or "").upper()
        n = float(r["notional"] or 0)
        if sym and side and n > 0:
            out.append(
                {
                    "profile_id": int(r["profile_id"]),
                    "symbol": sym,
                    "side": side,
                    "notional": n,
                }
            )
    return out


def _return_series(symbol: str, bars: int) -> Optional[pd.Series]:
    try:
        from moss_quant.kline_cache import load_cached

        df = load_cached(symbol, refresh=False)
        if df is None or len(df) < bars + 2:
            return None
        close = df["close"].astype(float).tail(bars + 1)
        ret = close.pct_change().dropna()
        return ret if len(ret) >= 16 else None
    except Exception as e:
        logger.debug("corr series %s: %s", symbol, e)
        return None


def btc_correlation(symbol: str) -> float:
    """与 BTC 15m 收益序列的 Pearson 相关；失败返回 0。"""
    sym = str(symbol or "").upper()
    if sym == _BTC_SYMBOL:
        return 1.0
    import time

    now = time.time()
    cached = _CORR_CACHE.get(sym)
    if cached and now - cached[1] < 3600:
        return cached[0]
    b = _return_series(_BTC_SYMBOL, cfg.MOSS_QUANT_PORTFOLIO_CORR_LOOKBACK_BARS)
    s = _return_series(sym, cfg.MOSS_QUANT_PORTFOLIO_CORR_LOOKBACK_BARS)
    if b is None or s is None:
        return 0.0
    n = min(len(b), len(s))
    if n < 16:
        return 0.0
    b_arr = b.tail(n).to_numpy()
    s_arr = s.tail(n).to_numpy()
    if np.std(b_arr) < 1e-12 or np.std(s_arr) < 1e-12:
        corr = 0.0
    else:
        corr = float(np.corrcoef(b_arr, s_arr)[0, 1])
    _CORR_CACHE[sym] = (corr, now)
    return corr


def check_portfolio_open_sim(
    *,
    budget: float,
    opens: List[Dict[str, Any]],
    symbol: str,
    side: str,
    proposed_notional: float,
    exclude_profile_id: Optional[int] = None,
) -> Tuple[bool, float, str]:
    """
    返回 (允许开仓, 名义缩放 0~1, 原因)。
    opens: [{profile_id?, symbol, side, notional}]
    """
    if not cfg.MOSS_QUANT_PORTFOLIO_RISK_ENABLED:
        return True, 1.0, "portfolio_risk_off"

    sym = str(symbol or "").upper()
    side = str(side or "").upper()
    prop = max(0.0, float(proposed_notional or 0))
    if prop <= 0:
        return False, 0.0, "invalid_notional"

    budget = float(budget or 0)
    if budget <= 0:
        return True, 1.0, "no_wallet_budget"

    same_side_notional = 0.0
    correlated_same_side = False
    thr = float(cfg.MOSS_QUANT_PORTFOLIO_CORR_THRESHOLD)

    for pos in opens:
        if exclude_profile_id and int(pos.get("profile_id") or 0) == int(
            exclude_profile_id
        ):
            continue
        if str(pos.get("side") or "").upper() != side:
            continue
        same_side_notional += float(pos.get("notional") or 0)
        other = str(pos.get("symbol") or "").upper()
        if other != sym and btc_correlation(other) >= thr and btc_correlation(sym) >= thr:
            correlated_same_side = True

    cap_pct = float(cfg.MOSS_QUANT_PORTFOLIO_MAX_SAME_SIDE_PCT)
    after = same_side_notional + prop
    if after > budget * cap_pct:
        return (
            False,
            0.0,
            f"同向敞口 {after:.0f} > 上限 {budget * cap_pct:.0f} ({cap_pct:.0%})",
        )

    if correlated_same_side:
        scale = float(cfg.MOSS_QUANT_PORTFOLIO_CORR_RISK_SCALE)
        return True, scale, "btc_high_corr_same_side_scale"

    return True, 1.0, "ok"


def check_portfolio_open(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    side: str,
    proposed_notional: float,
    exclude_profile_id: Optional[int] = None,
) -> Tuple[bool, float, str]:
    """纸面扫描：从 DB 读未平仓 + 钱包预算。"""
    budget = _wallet_budget_usdt(conn)
    opens = list_open_signal_exposure(conn)
    return check_portfolio_open_sim(
        budget=budget,
        opens=opens,
        symbol=symbol,
        side=side,
        proposed_notional=proposed_notional,
        exclude_profile_id=exclude_profile_id,
    )
