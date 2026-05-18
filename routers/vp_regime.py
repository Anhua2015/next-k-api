from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from models.api_models import VpRegimeScanBody
from utils.maintenance_auth import require_maintenance_token

logger = logging.getLogger(__name__)

router = APIRouter(tags=["vp_regime"])


@router.post("/api/vp-regime/scan")
async def post_vp_regime_scan(
    body: VpRegimeScanBody = Body(...),
    _: None = Depends(require_maintenance_token),
):
    """VP 量价环境扫描（同步，可能数十秒）；维护面板用。"""
    symbols_override = None
    if body.symbols and str(body.symbols).strip():
        symbols_override = [
            x.strip().upper() for x in str(body.symbols).split(",") if x.strip()
        ]

    def _work():
        from vp_regime_scanner import run_scan

        return run_scan(
            use_db=bool(body.persist),
            use_tg=bool(body.notify_tg),
            symbols_override=symbols_override,
            watchlist_request=True if body.watchlist else None,
            quiet=True,
        )

    try:
        return await run_in_threadpool(_work)
    except Exception as e:
        logger.exception("vp_regime scan failed: %s", e)
        raise HTTPException(status_code=500, detail="vp_regime_scan_failed") from e
