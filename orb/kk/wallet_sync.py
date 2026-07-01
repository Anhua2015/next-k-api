"""KK 复利钱包与 vnpy 成交持久化。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from orb.core.fees import trade_fee_usdt
from orb.core.kline_cache import norm_symbol
from orb.kk.config import KKConfig
from orb.kk.db import insert_trade, load_wallet, migrate_kk_tables, save_wallet


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sum_symbol_wallets(cur, symbols: list[str], *, default: float) -> float:
    total = 0.0
    for sym in symbols:
        total += load_wallet(cur, norm_symbol(sym), default=float(default))
    return round(total, 4)


def estimate_close_pnl(
    *,
    side: str,
    entry: float,
    exit_px: float,
    notional_usdt: float,
    kk: KKConfig,
) -> tuple[float, float, float]:
    """返回 (gross, fee, net) USDT。"""
    entry_v = float(entry or 0.0)
    exit_v = float(exit_px or 0.0)
    notion = float(notional_usdt or 0.0)
    if entry_v <= 0 or exit_v <= 0 or notion <= 0:
        return 0.0, 0.0, 0.0
    side_u = str(side).upper()
    if side_u == "LONG":
        gross = (exit_v - entry_v) / entry_v * notion
    else:
        gross = (entry_v - exit_v) / entry_v * notion
    fee = trade_fee_usdt(
        notion,
        entry_mode="stop",
        maker_bps=kk.fee_maker_bps,
        taker_bps=kk.fee_taker_bps,
    )
    net = round(float(gross) - float(fee), 4)
    return round(float(gross), 4), round(float(fee), 4), net


def record_vnpy_fill(
    *,
    symbol: str,
    event: str,
    side: str,
    price: float,
    volume: float,
    notional_usdt: float,
    session_date: str,
    bar_ms: int,
    kk: KKConfig,
    outcome: str = "",
    pnl_usdt: Optional[float] = None,
    pnl_gross: Optional[float] = None,
    fee_usdt: Optional[float] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> float:
    """写入 kk_trades；compound 时在 close 更新 kk_symbol_bots。返回该标 wallet。"""
    sym = norm_symbol(symbol)
    from accumulation_radar import init_db

    conn = init_db()
    try:
        cur = conn.cursor()
        migrate_kk_tables(cur)
        insert_trade(
            cur,
            {
                "session_date": session_date,
                "symbol": sym,
                "event": event,
                "side": side,
                "entry": price if event == "open" else None,
                "exit_px": price if event == "close" else None,
                "notional_usdt": notional_usdt,
                "pnl_usdt_gross": pnl_gross,
                "fee_usdt": fee_usdt,
                "pnl_usdt": pnl_usdt,
                "outcome": outcome or None,
                "detail": detail or {},
                "bar_ms": bar_ms,
                "created_at_utc": _utc_now(),
            },
        )
        wallet = load_wallet(cur, sym, default=float(kk.equity_usdt or 14.0))
        if kk.compound and event == "close" and pnl_usdt is not None:
            wallet = round(wallet + float(pnl_usdt), 4)
            save_wallet(cur, sym, wallet, now_utc=_utc_now())
        conn.commit()
        return wallet
    finally:
        conn.close()
