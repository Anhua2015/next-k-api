"""King Keltner 实盘辅助（官方 vnpy_binance 直连币安）。"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from orb.core.kline_cache import norm_symbol
from orb.core.protocol_ingest import live_ingest_succeeded
from orb.core.protocol_client import (
    ingest_signals,
    lookup_signal,
    reconcile_pending_entries,
    update_protective_sl,
)
from orb.kk.config import KKConfig
from orb.cta.strategies import KK_TRAILING_PCT

logger = logging.getLogger(__name__)

SOURCE_KK = "kk"
PLAY_KK = "KK"


def _binance_configured() -> bool:
    return bool(
        (os.getenv("BINANCE_API_KEY") or "").strip()
        and (os.getenv("BINANCE_API_SECRET") or "").strip()
    )


def live_enabled(kk: KKConfig) -> bool:
    if not kk.live_enabled:
        return False
    if not _binance_configured():
        logger.warning("[kk] KK_LIVE_ENABLED=1 but BINANCE_API_KEY/SECRET unset")
        return False
    return True


def _leverage(kk: KKConfig, orb_cfg) -> float:
    lev = float(kk.live_leverage or 0.0)
    if lev > 0:
        return lev
    return 5.0


def _margin_from_notional(notional_usdt: float, lev: float) -> float:
    n = max(0.0, float(notional_usdt or 0.0))
    if n > 0 and lev > 0:
        return n / lev
    return 0.0


def open_api_id(symbol: str, session_date: str, bar_ms: int) -> str:
    sym = norm_symbol(symbol)
    return f"kk:open:{sym}:{session_date}:{int(bar_ms)}"


def close_api_id(symbol: str, session_date: str, bar_ms: int, outcome: str) -> str:
    sym = norm_symbol(symbol)
    tag = str(outcome or "close").strip().lower()
    return f"kk:close:{sym}:{session_date}:{int(bar_ms)}:{tag}"


def bootstrap_sl_price(*, side: str, entry: float, sl: float = 0.0) -> float:
    sl_v = float(sl or 0.0)
    if sl_v > 0:
        return sl_v
    entry_v = float(entry or 0.0)
    if entry_v <= 0:
        return 0.0
    trail = float(KK_TRAILING_PCT) / 100.0
    if str(side).upper() == "LONG":
        return entry_v * (1.0 - trail)
    return entry_v * (1.0 + trail)


def build_open_payload(
    *,
    symbol: str,
    side: str,
    entry_price: float,
    notional_usdt: float,
    session_date: str,
    bar_ms: int,
    sl_price: Optional[float],
    kk: KKConfig,
    orb_cfg,
) -> Dict[str, Any]:
    lev = _leverage(kk, orb_cfg)
    margin = _margin_from_notional(notional_usdt, lev)
    api_id = open_api_id(symbol, session_date, bar_ms)
    sl = bootstrap_sl_price(side=side, entry=entry_price, sl=float(sl_price or 0.0))
    return {
        "source": SOURCE_KK,
        "api_signal_id": api_id,
        "symbol": norm_symbol(symbol),
        "side": str(side).upper(),
        "margin_usdt": round(margin, 4),
        "leverage": lev,
        "entry_price": float(entry_price),
        "sl_price": round(sl, 6) if sl > 0 else None,
        "play": PLAY_KK,
        "confidence": "high",
        "action": "open",
        "entry_type": "MARKET",
        "client_ref": api_id,
    }


def build_close_payload(
    *,
    symbol: str,
    side: str,
    session_date: str,
    bar_ms: int,
    outcome: str,
    close_price: Optional[float] = None,
) -> Dict[str, Any]:
    api_id = close_api_id(symbol, session_date, bar_ms, outcome)
    payload: Dict[str, Any] = {
        "source": SOURCE_KK,
        "api_signal_id": api_id,
        "symbol": norm_symbol(symbol),
        "side": str(side).upper(),
        "action": "close",
        "play": PLAY_KK,
        "client_ref": api_id,
    }
    tag = str(outcome or "").strip().lower()
    if close_price is not None and close_price > 0 and tag not in ("eod", "session_close"):
        payload["close_price"] = float(close_price)
    return payload


def sync_live_pending() -> None:
    try:
        reconcile_pending_entries()
    except Exception as exc:
        logger.warning("[kk] protocol reconcile failed: %s", exc)


def notify_open(
    trade: Dict[str, Any],
    *,
    symbol: str,
    session_date: str,
    kk: KKConfig,
    orb_cfg,
) -> Dict[str, Any]:
    if not live_enabled(kk):
        return {"skipped": True, "reason": "live_disabled"}
    side = str(trade.get("side") or "").upper()
    if side not in ("LONG", "SHORT"):
        return {"skipped": True, "reason": "invalid_side"}
    payload = build_open_payload(
        symbol=symbol,
        side=side,
        entry_price=float(trade.get("entry") or 0),
        notional_usdt=float(trade.get("notional_usdt") or 0),
        session_date=session_date,
        bar_ms=int(trade.get("ms") or 0),
        sl_price=float(trade.get("sl") or 0) or None,
        kk=kk,
        orb_cfg=orb_cfg,
    )
    return ingest_signals([payload])


def notify_close(
    trade: Dict[str, Any],
    *,
    symbol: str,
    session_date: str,
    kk: KKConfig,
) -> Dict[str, Any]:
    if not live_enabled(kk):
        return {"skipped": True, "reason": "live_disabled"}
    side = str(trade.get("side") or "").upper()
    payload = build_close_payload(
        symbol=symbol,
        side=side,
        session_date=session_date,
        bar_ms=int(trade.get("ms") or 0),
        outcome=str(trade.get("outcome") or "close"),
        close_price=float(trade.get("exit") or 0) or None,
    )
    return ingest_signals([payload])


def notify_trailing_sl(
    *,
    symbol: str,
    side: str,
    sl_price: float,
) -> Dict[str, Any]:
    if sl_price <= 0:
        return {"skipped": True, "reason": "no_sl"}
    raw = update_protective_sl(
        symbol=norm_symbol(symbol),
        side=str(side).upper(),
        sl_price=float(sl_price),
        source=SOURCE_KK,
    )
    if raw.get("ok"):
        return {"traded": 1, "details": [{"action": "traded", "sl_price": raw.get("sl_price")}]}
    return {"errors": 1, "details": [{"action": "error", "error": raw.get("error")}]}


def live_open_done(symbol: str, session_date: str, bar_ms: int) -> bool:
    try:
        row = lookup_signal(source=SOURCE_KK, api_signal_id=open_api_id(symbol, session_date, bar_ms))
    except Exception as exc:
        logger.warning("[kk] lookup open %s failed: %s", symbol, exc)
        return False
    if not row:
        return False
    return str(row.get("status") or "").lower() in ("traded", "submitted")


__all__ = [
    "SOURCE_KK",
    "bootstrap_sl_price",
    "build_close_payload",
    "build_open_payload",
    "close_api_id",
    "live_enabled",
    "live_ingest_succeeded",
    "live_open_done",
    "notify_close",
    "notify_open",
    "notify_trailing_sl",
    "open_api_id",
    "sync_live_pending",
]
