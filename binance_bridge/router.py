"""FastAPI router for the Binance live-trading bridge.

All endpoints are under /api/binance/ and require the maintenance token
(X-Maintenance-Token header or Authorization: Bearer <token>), consistent
with next-k-api's existing auth pattern.

Exception: GET /api/binance/health is public (liveness probe).

Endpoints appear in the unified /docs under tag 'binance-live-trading'.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from binance_bridge import db as _db
from binance_bridge.models import (
    ConfigUpdate,
    PnlSummaryOut,
    PositionOut,
    SignalBridgeResult,
    SignalLogOut,
    StatusOut,
)
from utils.maintenance_auth import require_maintenance_token

logger = logging.getLogger("binance_bridge.router")

router = APIRouter(
    prefix="/api/binance",
    tags=["binance-live-trading"],
)

# Keys that must be masked in GET /config responses.
_SENSITIVE_KEYS = {"binance_api_key", "binance_api_secret"}


# ---------------------------------------------------------------------------
# Health (public)
# ---------------------------------------------------------------------------

@router.get(
    "/health",
    summary="Binance bridge liveness probe",
    include_in_schema=True,
)
async def health():
    """Unauthenticated liveness probe for the Binance bridge module."""
    return {"status": "ok", "module": "binance-bridge"}


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@router.get(
    "/status",
    response_model=StatusOut,
    summary="Bridge status summary",
    dependencies=[Depends(require_maintenance_token)],
)
async def get_status():
    """Return trading enabled/disabled state, open position count, and key config."""
    cfg = _db.get_all_config()
    open_pos = len(_db.get_open_positions())
    return StatusOut(
        enabled=cfg.get("enabled", "false"),
        testnet=cfg.get("testnet", "false"),
        open_positions=open_pos,
        max_positions=cfg.get("max_positions", "3"),
        position_expire_hours=cfg.get("position_expire_hours", "4"),
        api_key_set=bool(cfg.get("binance_api_key", "").strip()),
    )


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@router.get(
    "/config",
    summary="Read bridge configuration",
    dependencies=[Depends(require_maintenance_token)],
)
async def get_config():
    """Return all config key/value pairs. Sensitive values (API key/secret) are masked."""
    cfg = _db.get_all_config()
    masked = {k: ("****" if k in _SENSITIVE_KEYS and v else v) for k, v in cfg.items()}
    return masked


@router.post(
    "/config",
    summary="Update bridge configuration",
    dependencies=[Depends(require_maintenance_token)],
)
async def update_config(body: ConfigUpdate):
    """Batch-update one or more config keys.

    Example pairs: `{"enabled": "true", "margin_usdt": "200"}`.
    Sensitive key values are redacted from the response log.
    """
    sanitized = {k: ("****" if k in _SENSITIVE_KEYS else v) for k, v in body.pairs.items()}
    logger.info("binance config update keys=%s", list(sanitized.keys()))
    _db.set_config_batch(body.pairs)
    return {"ok": True, "updated": list(body.pairs.keys())}


# ---------------------------------------------------------------------------
# Signal log
# ---------------------------------------------------------------------------

@router.get(
    "/signals",
    response_model=List[SignalLogOut],
    summary="List signal log entries",
    dependencies=[Depends(require_maintenance_token)],
)
async def list_signals(
    limit: int = Query(100, ge=1, le=1000, description="Page size"),
    offset: int = Query(0, ge=0, description="Page offset"),
):
    """Return bridge signal log in reverse-chronological order.

    Each entry records a ZCT signal and the action taken: traded / skipped_* / error.
    """
    rows = _db.list_signals(limit=limit, offset=offset)
    return rows


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

@router.get(
    "/positions",
    response_model=List[PositionOut],
    summary="List positions (open or closed)",
    dependencies=[Depends(require_maintenance_token)],
)
async def list_positions(
    status: Optional[str] = Query(
        None,
        description="Filter by status: 'open' | 'closed' | omit for all",
    ),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """Return positions with full P&L details.

    - **status=open**: currently running positions (entry filled, SL/TP active).
    - **status=closed**: settled positions with close_reason (tp/sl/expired/manual).
    - Omit status for all positions.
    """
    if status and status not in ("open", "closed"):
        raise HTTPException(status_code=400, detail="status must be 'open' or 'closed'")
    rows = _db.list_positions(status=status, limit=limit, offset=offset)
    return rows


@router.get(
    "/positions/{position_id}",
    response_model=PositionOut,
    summary="Get a single position by ID",
    dependencies=[Depends(require_maintenance_token)],
)
async def get_position(position_id: int):
    """Return full detail for a single position, including P&L breakdown."""
    pos = _db.get_position_by_id(position_id)
    if pos is None:
        raise HTTPException(status_code=404, detail="position_not_found")
    return pos


# ---------------------------------------------------------------------------
# P&L summary
# ---------------------------------------------------------------------------

@router.get(
    "/pnl/summary",
    response_model=PnlSummaryOut,
    summary="Aggregated P&L summary",
    dependencies=[Depends(require_maintenance_token)],
)
async def pnl_summary():
    """Return total trades, win/loss counts, cumulative PnL, and last 30 days daily PnL.

    P&L formula:
    - LONG:  ret = close_price / entry_price - 1
    - SHORT: ret = entry_price / close_price - 1
    - pnl_pct = ret × leverage × 100 (%)
    - pnl_usdt = qty × |close_price - entry_price|  (signed)
    """
    return _db.pnl_summary()


# ---------------------------------------------------------------------------
# Manual signal bridge trigger
# ---------------------------------------------------------------------------

@router.post(
    "/trigger-signal-scan",
    response_model=SignalBridgeResult,
    summary="Manually trigger signal bridge processing",
    dependencies=[Depends(require_maintenance_token)],
)
async def trigger_signal_scan():
    """Manually run signal_bridge.on_scan_complete() without waiting for next ZCT scan.

    Useful for testing or recovering from a missed scan event.
    Returns a summary of how many signals were traded/skipped/errored.
    """
    from binance_bridge.signal_bridge import on_scan_complete

    def _run():
        return on_scan_complete()

    try:
        result = await run_in_threadpool(_run)
        return result
    except Exception as exc:
        logger.exception("trigger-signal-scan failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
