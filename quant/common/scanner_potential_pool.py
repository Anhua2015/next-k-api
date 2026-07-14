"""Read breakoutscanner Tier-A potential pool for vnpy lanes (spot)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, List

from quant.common.kline_cache import norm_symbol

_DEFAULT_REL = Path(__file__).resolve().parents[3] / "breakoutscanner" / "data_cache" / "potential_pool.json"


def default_potential_pool_path() -> Path:
    raw = (os.getenv("SCANNER_POTENTIAL_POOL_PATH") or "").strip()
    if raw:
        return Path(raw)
    return _DEFAULT_REL


def load_potential_pool_payload() -> dict[str, Any]:
    path = default_potential_pool_path()
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _dedupe(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for s in symbols:
        sym = norm_symbol(s) if s else ""
        if sym and sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out


def current_tier_a_pool() -> List[str]:
    """Today's Tier-A pool only (matches research `in_pool`)."""
    payload = load_potential_pool_payload()
    symbols: list[str] = []
    for key in ("potential_pool", "tier_a_pool"):
        raw = payload.get(key) or []
        if isinstance(raw, list):
            symbols.extend(str(s).strip() for s in raw if str(s).strip())
    return _dedupe(symbols)


def symbol_in_tier_a_pool(symbol: str) -> bool:
    sym = norm_symbol(symbol)
    return bool(sym) and sym in set(current_tier_a_pool())


def load_tier_a_symbols(*, max_symbols: int = 0, prefer_alerts: bool = False) -> List[str]:
    """
    Universe for strategy registration.

    prefer_alerts=True → only entry_alerts.
    Else: current pool first, then memory_30d / top15 (warmup), de-duped.
    """
    payload = load_potential_pool_payload()
    symbols: list[str] = []
    if prefer_alerts:
        raw = payload.get("entry_alerts") or []
        if isinstance(raw, list):
            for row in raw:
                if isinstance(row, dict) and row.get("symbol"):
                    symbols.append(str(row["symbol"]).strip())
                elif isinstance(row, str) and row.strip():
                    symbols.append(row.strip())
    else:
        symbols.extend(current_tier_a_pool())
        for key in ("memory_30d", "priority_top15"):
            raw = payload.get(key) or []
            if isinstance(raw, list):
                symbols.extend(str(s).strip() for s in raw if str(s).strip())
    out = _dedupe(symbols)
    if max_symbols > 0:
        return out[:max_symbols]
    return out


def pool_entry_alert(symbol: str) -> dict[str, Any] | None:
    sym = norm_symbol(symbol)
    if not sym:
        return None
    for row in load_potential_pool_payload().get("entry_alerts") or []:
        if not isinstance(row, dict):
            continue
        if norm_symbol(str(row.get("symbol") or "")) == sym:
            return row
    return None


def _payload_pool_size(payload: dict[str, Any]) -> int:
    if payload.get("pool_size") is not None:
        try:
            return int(payload.get("pool_size") or 0)
        except (TypeError, ValueError):
            pass
    return len(current_tier_a_pool())


def pool_ok_for_entry(*, max_pool: int = 10) -> bool:
    """True when current Tier-A pool is non-empty and size <= max_pool."""
    payload = load_potential_pool_payload()
    if not payload:
        return False
    if "pool_ok_for_entry" in payload:
        return bool(payload.get("pool_ok_for_entry"))
    size = _payload_pool_size(payload)
    return 0 < size <= int(max_pool)


def potential_pool_meta() -> dict[str, Any]:
    path = default_potential_pool_path()
    if not path.is_file():
        return {"path": str(path), "exists": False}
    payload = load_potential_pool_payload()
    if not payload:
        return {"path": str(path), "exists": True, "valid": False}
    return {
        "path": str(path),
        "exists": True,
        "valid": True,
        "updated_at": payload.get("updated_at"),
        "as_of": payload.get("as_of"),
        "strategy": payload.get("strategy"),
        "market": payload.get("market", "spot"),
        "pool_size": _payload_pool_size(payload),
        "pool_ok_for_entry": pool_ok_for_entry(),
        "alerts": len(payload.get("entry_alerts") or []),
        "current_pool": current_tier_a_pool(),
    }


def merge_with_file_symbols(
    base: List[str],
    *,
    use_pool: bool,
    prefer_alerts: bool,
    max_symbols: int,
) -> List[str]:
    if not use_pool:
        return _dedupe([str(s) for s in base])
    pool = load_tier_a_symbols(max_symbols=max_symbols, prefer_alerts=prefer_alerts)
    if pool:
        return pool
    return _dedupe([str(s) for s in base])
