from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(tags=["s2", "s6"])

def _filter_s2_funding_signals_last_days(signals: List[Dict[str, Any]], days: int = 2) -> List[Dict[str, Any]]:
    """Keep entries with recorded_at within last `days` (Asia/Shanghai cutoff)."""
    cst = timezone(timedelta(hours=8))
    cutoff = datetime.now(cst) - timedelta(days=days)
    out: List[Dict[str, Any]] = []
    for row in signals:
        if not isinstance(row, dict):
            continue
        ts = row.get("recorded_at")
        if not ts or not isinstance(ts, str):
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=cst)
            if dt >= cutoff:
                out.append(row)
        except Exception:
            continue
    out.sort(key=lambda r: str(r.get("recorded_at", "")), reverse=True)
    return out


def _s6_candidates_s_only(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """归档里 candidates 仅展示 S；旧数据含 A/B 时在 API 层剥掉。"""
    out: List[Dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        row = dict(r)
        c = row.get("candidates")
        if isinstance(c, list):
            row["candidates"] = [
                x for x in c if isinstance(x, dict) and x.get("strength") == "S"
            ]
            row["candidate_count"] = len(row["candidates"])
        out.append(row)
    return out
def _s6_signals_history_path() -> Path:
    return Path(__file__).resolve().parent / "s6_signals_history.json"


def _s6_trades_json_path() -> Path:
    return Path(__file__).resolve().parent / "trades.json"


def _s6_compute_balance_usd(trades_root: Dict[str, Any]) -> Tuple[float, float]:
    """(balance_after_closed, initial_balance) — 与 s6 get_balance 一致。"""
    initial = float(trades_root.get("initial_balance", 100.0))
    bal = initial
    trades = trades_root.get("trades")
    if not isinstance(trades, list):
        return bal, initial
    for t in trades:
        if isinstance(t, dict) and t.get("status") == "closed" and t.get("pnl_usd") is not None:
            try:
                bal += float(t["pnl_usd"])
            except (TypeError, ValueError):
                continue
    return bal, initial
@router.get("/api/s6/autonomous-alpha")
async def get_s6_autonomous_alpha():
    """
    s6 期货 Alpha：近 2 日每小时扫描归档 + 当前模拟持仓（trades.json）。
    """
    sig_path = _s6_signals_history_path()
    signals: List[Dict[str, Any]] = []
    if sig_path.is_file():
        try:
            raw = json.loads(sig_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("signals"), list):
                signals = raw["signals"]
        except Exception as e:
            logger.warning("s6 signals history read failed: %s", e)
            raise HTTPException(status_code=500, detail="s6_signals_corrupt")
    filtered = _filter_s2_funding_signals_last_days(signals, 2)
    filtered = _s6_candidates_s_only(filtered)

    trades_path = _s6_trades_json_path()
    open_positions: List[Dict[str, Any]] = []
    balance_usd = 100.0
    initial_balance = 100.0
    if trades_path.is_file():
        try:
            troot = json.loads(trades_path.read_text(encoding="utf-8"))
            if isinstance(troot, dict):
                balance_usd, initial_balance = _s6_compute_balance_usd(troot)
                for t in troot.get("trades") or []:
                    if isinstance(t, dict) and t.get("status") == "open":
                        open_positions.append(t)
        except Exception as e:
            logger.warning("s6 trades.json read failed: %s", e)

    return {
        "ok": True,
        "signals": filtered,
        "day_window": 2,
        "source": "disk",
        "count": len(filtered),
        "initial_balance": initial_balance,
        "balance_usd": round(balance_usd, 4),
        "open_positions": open_positions,
        "open_count": len(open_positions),
    }

@router.get("/api/s2/funding-signals")
async def get_s2_funding_signals():
    """
    返回近 2 日「费率刚转负 + OI 涨」强信号（与 TG 同源）。
    持久化：accumulation.db 表 s2_funding_signals（原 JSON 由脚本启动时迁移）。
    """
    try:
        from s2_oi_funding_rate_scanner import get_s2_funding_signals_for_api

        return get_s2_funding_signals_for_api(2)
    except Exception as e:
        logger.warning("s2 funding signals read failed: %s", e)
        raise HTTPException(status_code=500, detail="s2_signals_db_error")

