"""币安账户设置（KK vnpy 直连，与 Protocol 无关）。"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
import urllib.parse
from typing import Any, Dict, List, Optional

import requests

from binance_fapi import FAPI
from orb.core.kline_cache import norm_symbol
from orb.kk.config import KKConfig
from orb.kk.live_exec import _leverage

logger = logging.getLogger(__name__)


def _api_key() -> str:
    return (os.getenv("BINANCE_API_KEY") or "").strip()


def _api_secret() -> bytes:
    return (os.getenv("BINANCE_API_SECRET") or "").strip().encode()


def _signed_get(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    key = _api_key()
    secret = _api_secret()
    if not key or not secret:
        raise RuntimeError("BINANCE_API_KEY/SECRET not configured")
    payload = dict(params or {})
    payload["timestamp"] = int(time.time() * 1000)
    query = urllib.parse.urlencode(sorted(payload.items()))
    sig = hmac.new(secret, query.encode(), hashlib.sha256).hexdigest()
    url = f"{FAPI}{path}?{query}&signature={sig}"
    resp = requests.get(url, headers={"X-MBX-APIKEY": key}, timeout=15)
    if resp.status_code >= 400:
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        raise RuntimeError(f"Binance {path} HTTP {resp.status_code}: {body}")
    return resp.json()


def _signed_post(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    key = _api_key()
    secret = _api_secret()
    if not key or not secret:
        raise RuntimeError("BINANCE_API_KEY/SECRET not configured")
    payload = dict(params)
    payload["timestamp"] = int(time.time() * 1000)
    query = urllib.parse.urlencode(sorted(payload.items()))
    sig = hmac.new(secret, query.encode(), hashlib.sha256).hexdigest()
    url = f"{FAPI}{path}?{query}&signature={sig}"
    resp = requests.post(url, headers={"X-MBX-APIKEY": key}, timeout=15)
    if resp.status_code >= 400:
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        raise RuntimeError(f"Binance {path} HTTP {resp.status_code}: {body}")
    return resp.json()


def set_symbol_leverage(symbol: str, leverage: int) -> None:
    sym = norm_symbol(symbol)
    lev = max(1, int(leverage))
    try:
        _signed_post("/fapi/v1/leverage", {"symbol": sym, "leverage": lev})
        logger.info("[kk-vnpy] leverage %s -> %sx", sym, lev)
    except RuntimeError as exc:
        msg = str(exc)
        if "-4028" in msg:
            return
        raise


def set_symbol_margin_isolated(symbol: str) -> None:
    sym = norm_symbol(symbol)
    try:
        _signed_post("/fapi/v1/marginType", {"symbol": sym, "marginType": "ISOLATED"})
    except RuntimeError as exc:
        msg = str(exc)
        if "-4046" in msg or "-4067" in msg or "No need to change margin type" in msg:
            return
        raise


def ensure_one_way_mode() -> None:
    """账户设为单向持仓（vnpy 仅支持 one-way）。"""
    try:
        _signed_post("/fapi/v1/positionSide/dual", {"dualSidePosition": "false"})
        logger.info("[kk-vnpy] position mode -> one-way")
    except RuntimeError as exc:
        msg = str(exc)
        if "-4059" in msg or "No need to change" in msg:
            return
        raise


def fetch_position_amounts(symbols: List[str]) -> Dict[str, float]:
    """symbol -> 净持仓张数（多为正、空为负）。"""
    want = {norm_symbol(s) for s in symbols}
    rows = _signed_get("/fapi/v2/positionRisk", {})
    out: Dict[str, float] = {}
    if not isinstance(rows, list):
        return out
    for row in rows:
        sym = norm_symbol(str(row.get("symbol") or ""))
        if sym not in want:
            continue
        out[sym] = float(row.get("positionAmt") or 0.0)
    return out


def ensure_pool_leverage(symbols: List[str], kk: KKConfig) -> None:
    ensure_one_way_mode()
    lev = int(_leverage(kk, kk.orb_session_cfg()))
    for raw in symbols:
        sym = norm_symbol(raw)
        set_symbol_margin_isolated(sym)
        set_symbol_leverage(sym, lev)
