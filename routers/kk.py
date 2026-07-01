"""King Keltner 策略状态 API。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from orb.kk.config import KKConfig
from orb.kk.live_exec import live_enabled

router = APIRouter(prefix="/api/kk", tags=["king_keltner"])


@router.get("/status")
async def kk_status() -> Dict[str, Any]:
    kk = KKConfig.from_env()
    out: Dict[str, Any] = {
        "lane": kk.lane,
        "engine": kk.engine,
        "enabled": kk.enabled,
        "live_enabled": kk.live_enabled,
        "live_active": live_enabled(kk),
        "symbols": kk.symbol_list(),
        "equity_usdt": kk.equity_usdt,
        "risk_pct": kk.risk_pct,
        "max_notional_usdt": kk.max_notional_usdt,
        "max_open_positions": kk.max_open_positions,
        "compound": kk.compound,
        "shadow": kk.shadow,
        "macro_filter": kk.macro_filter,
        "rth_only": kk.rth_only,
        "eod_flat": kk.eod_flat,
        "scheduler_enabled": kk.scheduler_enabled,
    }
    try:
        from orb.kk.vnpy.supervisor import kk_vnpy_supervisor

        out["vnpy"] = {
            "running": kk_vnpy_supervisor.is_running,
            "bootstrap": kk_vnpy_supervisor.last_status,
        }
    except Exception as exc:
        out["vnpy"] = {"running": False, "error": str(exc)}
    return out
