"""币安 topMovers 动量信号解析（公开接口，无需 API Key）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import momentum_config as cfg
from binance_fapi import fetch_top_movers


def to_binance_symbol(raw: str) -> str:
    """LUMIA / LUMIAUSDT / LUMIA/USDT:USDT → LUMIAUSDT。"""
    if not raw:
        return ""
    s = str(raw).strip().upper()
    if s.endswith("/USDT:USDT"):
        return s.replace("/USDT:USDT", "USDT")
    if s.endswith("/USDT"):
        return s.replace("/USDT", "USDT")
    if s.endswith("USDT"):
        return s
    return f"{s}USDT"


def filter_symbol(
    symbol: str,
    *,
    blacklist: Sequence[str] | None = None,
    allow_usdc: bool | None = None,
) -> bool:
    bl = blacklist if blacklist is not None else cfg.MOM_BLACKLIST
    usdc_ok = cfg.MOM_ALLOW_USDC if allow_usdc is None else allow_usdc
    sym = str(symbol or "").upper()
    if not sym:
        return False
    if any(b and b.upper() in sym for b in bl):
        return False
    if not usdc_ok and "USDC" in sym:
        return False
    return True


def pick_momentum_targets(
    movers: List[Dict[str, Any]],
    *,
    long_event: str | None = None,
    short_event: str | None = None,
    blacklist: Sequence[str] | None = None,
    allow_usdc: bool | None = None,
) -> Tuple[Optional[str], Optional[str], Dict[str, Any]]:
    """返回 (long_symbol, short_symbol, debug_meta)。"""
    lev = (long_event or cfg.MOM_LONG_EVENT).strip()
    sev = (short_event or cfg.MOM_SHORT_EVENT).strip()
    valid = [
        x
        for x in movers
        if isinstance(x, dict) and filter_symbol(str(x.get("symbol") or ""), blacklist=blacklist, allow_usdc=allow_usdc)
    ]
    ordered = sorted(
        valid,
        key=lambda x: int(x.get("createTimestamp") or 0),
        reverse=True,
    )
    long_sym = None
    short_sym = None
    long_evt: Dict[str, Any] | None = None
    short_evt: Dict[str, Any] | None = None
    for x in ordered:
        et = str(x.get("eventType") or "")
        sym = to_binance_symbol(str(x.get("symbol") or ""))
        if not sym:
            continue
        if long_sym is None and et == lev:
            long_sym = sym
            long_evt = dict(x)
        if short_sym is None and et == sev:
            short_sym = sym
            short_evt = dict(x)
        if long_sym and short_sym:
            break
    meta = {
        "movers_total": len(movers),
        "movers_valid": len(valid),
        "long_event": lev,
        "short_event": sev,
        "long_event_raw": long_evt,
        "short_event_raw": short_evt,
    }
    return long_sym, short_sym, meta


def fetch_momentum_targets(
    *,
    long_event: str | None = None,
    short_event: str | None = None,
) -> Tuple[Optional[str], Optional[str], Dict[str, Any]]:
    movers = fetch_top_movers()
    if not movers:
        return None, None, {"error": "empty_top_movers", "movers_total": 0}
    long_sym, short_sym, meta = pick_momentum_targets(
        movers,
        long_event=long_event,
        short_event=short_event,
    )
    return long_sym, short_sym, meta
