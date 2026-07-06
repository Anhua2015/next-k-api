"""Protocol ingest 结果判定（KK 等 lane 共用）。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from orb.core.kline_cache import norm_symbol


def ingest_detail_action(result: Optional[Dict[str, Any]]) -> str:
    if not isinstance(result, dict):
        return ""
    for detail in result.get("details") or []:
        action = str(detail.get("action") or "").lower()
        if action:
            return action
    return ""


def live_open_is_pending(result: Optional[Dict[str, Any]]) -> bool:
    return ingest_detail_action(result) == "submitted"


def live_ingest_succeeded(result: Optional[Dict[str, Any]]) -> bool:
    if result is None:
        return True
    action = ingest_detail_action(result)
    if action == "duplicate":
        return True
    if result.get("skipped") is True:
        return True
    if result.get("error"):
        return False
    if int(result.get("errors") or 0) > 0:
        return False
    if int(result.get("traded") or 0) >= 1:
        return True
    if action in ("traded", "submitted"):
        return True
    for detail in result.get("details") or []:
        act = str(detail.get("action") or "").lower()
        if act in ("traded", "submitted"):
            return True
        if act == "error":
            return False
    return False


def _close_signal_id(symbol: str, *, signal_id: int, tag: str) -> str:
    sym = norm_symbol(symbol)
    return f"orb:close:{sym}:{int(signal_id)}:{str(tag or 'resolve').strip().lower()}"


def build_close_payload(
    symbol: str,
    side: str,
    *,
    close_price: Optional[float] = None,
    play: Optional[str] = None,
    tag: str = "resolve",
    signal_id: Optional[int] = None,
) -> Dict[str, Any]:
    sym = norm_symbol(symbol)
    side_u = str(side).upper()
    sid = int(signal_id or 0)
    tag_s = str(tag or "resolve").strip().lower()
    api_id = _close_signal_id(sym, signal_id=sid, tag=tag_s) if sid > 0 else f"orb:close:{sym}:{tag_s}"
    payload: Dict[str, Any] = {
        "source": "orb",
        "api_signal_id": api_id,
        "symbol": sym,
        "side": side_u,
        "action": "close",
        "play": play or "ORB",
    }
    if close_price is not None and close_price > 0 and tag_s != "session_close":
        payload["close_price"] = float(close_price)
    return payload
