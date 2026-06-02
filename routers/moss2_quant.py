"""Moss2 lane API."""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from utils.maintenance_auth import require_maintenance_token

router = APIRouter(prefix="/api/moss2-quant", tags=["moss2-quant"])


class RobotUpsertRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    template: str = Field(..., min_length=1, max_length=32)
    layer_code: str = Field(..., min_length=1, max_length=8)
    enabled: bool = True
    candidate_symbols: List[str] = Field(default_factory=list)
    tactical_params: Dict[str, Any] = Field(default_factory=dict)


class SymbolLayerUpsertRequest(BaseModel):
    symbol: str = Field(..., min_length=2, max_length=24)
    layer_code: str = Field(..., min_length=1, max_length=8)
    score: Optional[float] = None
    note: Optional[str] = Field(None, max_length=256)


class SymbolLayerBatchUpsertRequest(BaseModel):
    items: List[SymbolLayerUpsertRequest] = Field(default_factory=list)


class RunScanRequest(BaseModel):
    refresh_klines: bool = False


class BacktestRequest(BaseModel):
    top_n_per_robot: int = Field(3, ge=1, le=10)
    refresh_klines: bool = False


class SyncFromDailyRequest(BaseModel):
    batch_id: Optional[int] = None
    max_symbols: int = Field(30, ge=1, le=200)
    auto_assign_robots: bool = True


def _conn():
    from accumulation_radar import init_db

    c = init_db()
    c.row_factory = sqlite3.Row
    return c


def _validate_template(name: str) -> str:
    normalized = name.strip().lower()
    allowed = {"trend", "momentum", "mean_revert", "balanced"}
    if normalized not in allowed:
        raise HTTPException(status_code=400, detail=f"unsupported template: {name}")
    return normalized


@router.get("/health")
def health():
    from moss2_quant import config as cfg

    return {
        "ok": True,
        "lane": "moss2_quant",
        "enabled": bool(cfg.MOSS2_QUANT_ENABLED),
        "scheduler_enabled": bool(cfg.MOSS2_QUANT_SCHEDULER_ENABLED),
    }


@router.get("/config")
def get_runtime_config():
    from moss2_quant import config as cfg

    return {
        "enabled": bool(cfg.MOSS2_QUANT_ENABLED),
        "scheduler_enabled": bool(cfg.MOSS2_QUANT_SCHEDULER_ENABLED),
        "robot_target": int(cfg.MOSS2_QUANT_ROBOT_TARGET),
        "symbol_pool_target": int(cfg.MOSS2_QUANT_SYMBOL_POOL_TARGET),
        "switch_cooldown_minutes": int(cfg.MOSS2_QUANT_SWITCH_COOLDOWN_MINUTES),
    }


@router.get("/robots")
def list_moss2_robots():
    from moss2_quant.db import list_open_positions, list_robots

    conn = _conn()
    try:
        return {
            "ok": True,
            "items": list_robots(conn),
            "open_positions": list_open_positions(conn),
        }
    finally:
        conn.close()


@router.post("/robots/upsert")
def upsert_moss2_robot(req: RobotUpsertRequest, _: bool = Depends(require_maintenance_token)):
    from moss2_quant.db import upsert_robot

    conn = _conn()
    try:
        item = upsert_robot(
            conn,
            name=req.name,
            template=_validate_template(req.template),
            layer_code=req.layer_code,
            candidate_symbols=[s.strip().upper() for s in req.candidate_symbols if s.strip()],
            tactical_params=req.tactical_params,
            enabled=req.enabled,
        )
        return {"ok": True, "item": item}
    finally:
        conn.close()


@router.get("/symbol-layers")
def list_moss2_symbol_layers():
    from moss2_quant.db import list_symbol_layers

    conn = _conn()
    try:
        return {"ok": True, "items": list_symbol_layers(conn)}
    finally:
        conn.close()


@router.post("/symbol-layers/upsert")
def upsert_moss2_symbol_layer(req: SymbolLayerUpsertRequest, _: bool = Depends(require_maintenance_token)):
    from moss2_quant.db import upsert_symbol_layer

    conn = _conn()
    try:
        item = upsert_symbol_layer(
            conn,
            symbol=req.symbol,
            layer_code=req.layer_code,
            score=req.score,
            note=req.note,
        )
        return {"ok": True, "item": item}
    finally:
        conn.close()


@router.post("/symbol-layers/batch-upsert")
def batch_upsert_moss2_symbol_layers(
    req: SymbolLayerBatchUpsertRequest,
    _: bool = Depends(require_maintenance_token),
):
    from moss2_quant.db import upsert_symbol_layer

    if not req.items:
        return {"ok": True, "upserted": 0, "items": []}
    conn = _conn()
    try:
        out = []
        for item in req.items:
            out.append(
                upsert_symbol_layer(
                    conn,
                    symbol=item.symbol,
                    layer_code=item.layer_code,
                    score=item.score,
                    note=item.note,
                )
            )
        return {"ok": True, "upserted": len(out), "items": out}
    finally:
        conn.close()


@router.post("/seed/recommended")
def seed_recommended(_: bool = Depends(require_maintenance_token)):
    from moss2_quant.service import seed_recommended_setup

    conn = _conn()
    try:
        return seed_recommended_setup(conn)
    finally:
        conn.close()


@router.post("/run-scan")
def run_scan(req: RunScanRequest, _: bool = Depends(require_maintenance_token)):
    from moss2_quant.service import run_scan_once

    conn = _conn()
    try:
        return run_scan_once(conn, refresh_klines=req.refresh_klines)
    finally:
        conn.close()


@router.get("/scan-runs")
def list_scan_runs(limit: int = 20):
    from moss2_quant.db import list_scan_runs as _list_scan_runs

    conn = _conn()
    try:
        return {"ok": True, "items": _list_scan_runs(conn, limit=max(1, min(100, int(limit))))}
    finally:
        conn.close()


@router.post("/backtest")
def run_backtest(req: BacktestRequest, _: bool = Depends(require_maintenance_token)):
    from moss2_quant.service import run_portfolio_backtest

    conn = _conn()
    try:
        return run_portfolio_backtest(
            conn,
            top_n_per_robot=req.top_n_per_robot,
            refresh_klines=req.refresh_klines,
        )
    finally:
        conn.close()


@router.post("/sync-from-daily-optimize")
def sync_from_daily_optimize(req: SyncFromDailyRequest, _: bool = Depends(require_maintenance_token)):
    from moss2_quant import config as cfg
    from moss2_quant.service import sync_from_daily_optimize as _sync

    if not bool(cfg.MOSS2_QUANT_ALLOW_DAILY_OPTIMIZE_SYNC):
        raise HTTPException(
            status_code=403,
            detail="moss2_daily_optimize_sync_disabled",
        )

    conn = _conn()
    try:
        return _sync(
            conn,
            batch_id=req.batch_id,
            max_symbols=req.max_symbols,
            auto_assign_robots=req.auto_assign_robots,
        )
    finally:
        conn.close()


@router.post("/sync-from-builtin-core-list")
def sync_from_builtin_core_list(
    auto_assign_robots: bool = True,
    _: bool = Depends(require_maintenance_token),
):
    from moss2_quant.service import sync_from_builtin_core_list as _sync_core

    conn = _conn()
    try:
        return _sync_core(conn, auto_assign_robots=bool(auto_assign_robots))
    finally:
        conn.close()

