"""开仓前轻量过滤：资金费率极端等（不替代 composite 信号）。"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Dict, List, Optional

from moss_quant import config as cfg

logger = logging.getLogger(__name__)


def _funding_rate_from_db(conn: Optional[sqlite3.Connection], symbol: str) -> Optional[float]:
    if conn is None:
        return None
    try:
        row = conn.execute(
            """SELECT funding_rate FROM s2_funding_signals
               WHERE symbol=? ORDER BY recorded_at DESC LIMIT 1""",
            (symbol.upper(),),
        ).fetchone()
        if row and row[0] is not None:
            return float(row[0])
    except Exception:
        pass
    return None


def _funding_rate_live(symbol: str) -> Optional[float]:
    try:
        from accumulation_radar import api_get

        data = api_get("/fapi/v1/premiumIndex", {"symbol": symbol.upper()})
        if isinstance(data, dict) and data.get("lastFundingRate") is not None:
            return float(data["lastFundingRate"])
    except Exception as e:
        logger.debug("funding live %s: %s", symbol, e)
    return None


def _oi_radar_row(symbol: str) -> Optional[Dict[str, Any]]:
    """从 oi_radar_snapshot.json 读取标的 OI/价格变化（与收筹雷达同源）。"""
    sym = str(symbol or "").upper()
    base = sym.replace("USDT", "")
    try:
        from accumulation_radar import OI_RADAR_SNAPSHOT_PATH

        if not OI_RADAR_SNAPSHOT_PATH.is_file():
            return None
        raw = json.loads(OI_RADAR_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        for row in raw.get("coin_data") or []:
            if not isinstance(row, dict):
                continue
            if str(row.get("sym") or "").upper() == sym:
                return row
            if str(row.get("coin") or "").upper() == base:
                return row
    except Exception as e:
        logger.debug("oi radar row %s: %s", sym, e)
    return None


def _oi_spike_flat_price(row: Dict[str, Any]) -> bool:
    """6h OI 增幅大但价格几乎不动 → 假突破风险。"""
    d6h = float(row.get("d6h") or 0)
    px = abs(float(row.get("px_chg") or 0))
    return d6h >= float(cfg.MOSS_QUANT_GATE_OI_D6H_MIN) and px <= float(
        cfg.MOSS_QUANT_GATE_OI_PX_FLAT_MAX
    )


def entry_trade_gate(
    symbol: str,
    *,
    side: str,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """
    返回 threshold_bump（提高开仓门槛）、allowed、reasons。
    不直接禁止时仅抬高 entry_threshold。
    """
    sym = str(symbol or "").upper()
    side_u = str(side or "").upper()
    bump = 0.0
    reasons: List[str] = []

    if cfg.MOSS_QUANT_GATE_FUNDING_EXTREME:
        fr = _funding_rate_from_db(conn, sym)
        if fr is None:
            fr = _funding_rate_live(sym)
        if fr is not None:
            extreme = float(cfg.MOSS_QUANT_GATE_FUNDING_ABS_MAX)
            # 做多时费率过正 = 多头拥挤；做空时费率过负 = 空头拥挤
            if side_u == "LONG" and fr > extreme:
                bump += float(cfg.MOSS_QUANT_GATE_FUNDING_BUMP)
                reasons.append(f"funding_high_long_{fr:.4f}")
            elif side_u == "SHORT" and fr < -extreme:
                bump += float(cfg.MOSS_QUANT_GATE_FUNDING_BUMP)
                reasons.append(f"funding_low_short_{fr:.4f}")

    if cfg.MOSS_QUANT_GATE_OI_SPIKE:
        oi_row = _oi_radar_row(sym)
        if oi_row and _oi_spike_flat_price(oi_row):
            bump += float(cfg.MOSS_QUANT_GATE_OI_BUMP)
            reasons.append(
                f"oi_spike_flat_d6h={float(oi_row.get('d6h') or 0):.1f}_px={float(oi_row.get('px_chg') or 0):.1f}"
            )

    allowed = True
    if bump >= float(cfg.MOSS_QUANT_GATE_BLOCK_BUMP) and cfg.MOSS_QUANT_GATE_HARD_BLOCK:
        allowed = False
        reasons.append("gate_hard_block")

    return {
        "allowed": allowed,
        "threshold_bump": round(bump, 4),
        "reasons": reasons,
    }


def effective_entry_threshold(
    base_threshold: float,
    *,
    gate_bump: float = 0.0,
    intraday_bump: float = 0.0,
) -> float:
    t = float(base_threshold) + float(gate_bump) + float(intraday_bump)
    return round(max(0.05, min(0.75, t)), 4)


def intraday_threshold_bump_from_pnl(pnl_pct: float) -> float:
    """按 Profile 盈亏占本金比例抬高开仓门槛（纸面/纸面回测共用）。"""
    if not cfg.MOSS_QUANT_INTRADAY_ADJUST_ENABLED:
        return 0.0
    pct = float(pnl_pct)
    if pct <= -float(cfg.MOSS_QUANT_INTRADAY_DRAWDOWN_PCT):
        return float(cfg.MOSS_QUANT_INTRADAY_DRAWDOWN_BUMP)
    if pct >= float(cfg.MOSS_QUANT_INTRADAY_PROFIT_PCT):
        return float(cfg.MOSS_QUANT_INTRADAY_PROFIT_BUMP)
    return 0.0


def intraday_threshold_bump(
    conn: sqlite3.Connection,
    profile_id: int,
    *,
    profile_capital: Optional[float] = None,
) -> float:
    """当日该 Profile 回撤/盈利过大时微调开仓门槛。"""
    if not cfg.MOSS_QUANT_INTRADAY_ADJUST_ENABLED:
        return 0.0
    cap = float(profile_capital or cfg.MOSS_QUANT_PROFILE_CAPITAL)
    if cap <= 0:
        return 0.0
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """SELECT COALESCE(SUM(
                   CASE WHEN outcome IS NOT NULL THEN pnl_usdt ELSE 0 END
               ), 0) AS realized,
                  COALESCE(SUM(
                   CASE WHEN outcome IS NULL THEN unrealized_pnl_usdt ELSE 0 END
               ), 0) AS unrealized
           FROM moss_signals WHERE profile_id=?""",
        (int(profile_id),),
    ).fetchone()
    if not row:
        return 0.0
    pnl = float(row["realized"] or 0) + float(row["unrealized"] or 0)
    return intraday_threshold_bump_from_pnl(pnl / cap)
