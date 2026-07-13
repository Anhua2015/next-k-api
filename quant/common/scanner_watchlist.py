"""Read breakoutscanner watchlist for vnpy lanes."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

from quant.common.symbols import parse_symbol_list

_DEFAULT_REL = Path(__file__).resolve().parents[3] / "breakoutscanner" / "data_cache" / "watchlist.json"


def default_watchlist_path() -> Path:
    raw = (os.getenv("SCANNER_WATCHLIST_PATH") or "").strip()
    if raw:
        return Path(raw)
    return _DEFAULT_REL


def load_watchlist_payload() -> dict[str, Any]:
    path = default_watchlist_path()
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_scanner_watchlist(*, max_symbols: int = 0) -> List[str]:
    """Load symbol list exported by breakoutscanner watchlist job."""
    payload = load_watchlist_payload()
    symbols: list[str] = []
    raw = payload.get("symbols") or payload.get("symbol_list") or []
    if isinstance(raw, list):
        symbols = [str(s).upper().strip() for s in raw if str(s).strip()]
    if max_symbols > 0:
        return symbols[:max_symbols]
    return symbols


def symbol_watchlist_detail(symbol: str) -> dict[str, Any] | None:
    sym = str(symbol or "").upper().strip()
    if not sym:
        return None
    details = load_watchlist_payload().get("symbol_details") or {}
    if not isinstance(details, dict):
        return None
    row = details.get(sym)
    return row if isinstance(row, dict) else None


def symbol_risk_multiplier(symbol: str, *, default: float = 1.0) -> float:
    row = symbol_watchlist_detail(symbol)
    if not row:
        return float(default)
    try:
        return float(row.get("risk_mult", default))
    except (TypeError, ValueError):
        return float(default)


def watchlist_meta() -> dict:
    path = default_watchlist_path()
    if not path.is_file():
        return {"path": str(path), "exists": False}
    payload = load_watchlist_payload()
    if not payload:
        return {"path": str(path), "exists": True, "valid": False}
    return {
        "path": str(path),
        "exists": True,
        "valid": True,
        "updated_at": payload.get("updated_at"),
        "count": len(payload.get("symbols", [])),
        "resonance_timeframes": payload.get("resonance_timeframes"),
        "radar_timeframes": payload.get("radar_timeframes"),
        "execution_timeframes": payload.get("execution_timeframes"),
    }


def merge_symbol_pools(
    base_symbols: List[str],
    *,
    use_watchlist: bool,
    watchlist_max: int = 0,
) -> List[str]:
    """Intersect base pool with scanner watchlist when enabled."""
    base = parse_symbol_list("\n".join(base_symbols))
    if not use_watchlist:
        return base
    watch = load_scanner_watchlist(max_symbols=watchlist_max)
    if not watch:
        return []
    allow = {s.upper() for s in watch}
    return [s for s in base if s.upper() in allow]
