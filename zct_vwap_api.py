"""
ZCT VWAP 信号只读查询（accumulation.db · 表 zct_vwap_signals）。
供 GET /api/zct-vwap/signals 与 /api/zct-vwap/summary 使用。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from accumulation_radar import init_db

_SIGNAL_SELECT = """
    SELECT
        id,
        recorded_at_utc,
        symbol,
        play,
        side,
        confidence,
        regime,
        entry_price,
        entry_bar_open_ms,
        sl_price,
        tp_price,
        r_unit,
        COALESCE(virtual_notional_usdt, 100.0) AS virtual_notional_usdt,
        vwap,
        vwap_upper,
        vwap_lower,
        slope_bps,
        band_width_pct,
        vwap_crosses,
        ma_crosses,
        chop_score,
        outcome,
        outcome_at_utc,
        exit_price,
        pnl_r,
        pnl_usdt,
        reasons_json,
        manual_entry_price,
        manual_exit_price,
        manual_notes,
        notes
    FROM zct_vwap_signals
"""


def _manual_pnl_est_usdt(row: Dict[str, Any]) -> Optional[float]:
    """按实盘入/平仓价与名义 U 估算盈亏（与脚本虚拟公式一致）。"""
    side = row.get("side")
    en = row.get("manual_entry_price")
    ex = row.get("manual_exit_price")
    n = float(row.get("virtual_notional_usdt") or 100)
    if en is None or ex is None:
        return None
    try:
        entry_f = float(en)
        exit_f = float(ex)
    except (TypeError, ValueError):
        return None
    if entry_f <= 0 or n <= 0:
        return None
    if side == "LONG":
        return round(n * (exit_f - entry_f) / entry_f, 4)
    if side == "SHORT":
        return round(n * (entry_f - exit_f) / entry_f, 4)
    return None


def _display_status(row: Dict[str, Any]) -> str:
    oc = row.get("outcome")
    sl = row.get("sl_price")
    side = row.get("side")
    if oc:
        return {"win": "已平仓·盈利", "loss": "已平仓·止损", "expired": "已平仓·超时"}.get(
            str(oc), str(oc)
        )
    if side in ("LONG", "SHORT") and sl is not None:
        return "持仓中"
    return "观望"


def _parse_reasons(reasons_json: Optional[str]) -> List[str]:
    if not reasons_json:
        return []
    try:
        x = json.loads(reasons_json)
        return x if isinstance(x, list) else []
    except Exception:
        return []


def load_zct_vwap_signals(
    *,
    limit: int = 200,
    offset: int = 0,
    symbol: Optional[str] = None,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    """分页列出信号；status: all（默认）| open | settled。"""
    conn = init_db()
    conn.row_factory = sqlite3.Row
    try:
        where: List[str] = ["1=1"]
        params: List[Any] = []
        sym_u = (symbol or "").strip().upper()
        if sym_u:
            where.append("symbol = ?")
            params.append(sym_u)
        st = (status or "all").strip().lower()
        if st == "open":
            where.append(
                "outcome IS NULL AND sl_price IS NOT NULL "
                "AND side IN ('LONG','SHORT')"
            )
        elif st == "settled":
            where.append("outcome IS NOT NULL")
        elif st != "all":
            raise ValueError("status must be all, open, or settled")

        sql = (
            _SIGNAL_SELECT
            + " WHERE "
            + " AND ".join(where)
            + " ORDER BY id DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r["display_status"] = _display_status(r)
            r["reasons_preview"] = "; ".join(_parse_reasons(r.get("reasons_json")))[:240]
            r["manual_pnl_est_usdt"] = _manual_pnl_est_usdt(r)

        cur.execute(
            "SELECT COUNT(*) FROM zct_vwap_signals WHERE " + " AND ".join(where),
            params[:-2],
        )
        total_match = int(cur.fetchone()[0])

        return {
            "ok": True,
            "total": total_match,
            "limit": limit,
            "offset": offset,
            "items": rows,
        }
    finally:
        conn.close()


def patch_zct_vwap_manual(signal_id: int, updates: Dict[str, Any]) -> Dict[str, Any]:
    """更新实盘补充字段；updates 仅含 manual_* 键。"""
    allowed = ("manual_entry_price", "manual_exit_price", "manual_notes")
    keys = [k for k in updates if k in allowed]
    if not keys:
        raise ValueError("no updatable fields (manual_entry_price, manual_exit_price, manual_notes)")
    conn = init_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM zct_vwap_signals WHERE id = ?", (signal_id,))
        if cur.fetchone() is None:
            return {"ok": False, "error": "not_found"}
        sets = []
        vals: List[Any] = []
        for k in keys:
            sets.append(f"{k} = ?")
            vals.append(updates[k])
        vals.append(signal_id)
        cur.execute(
            f"UPDATE zct_vwap_signals SET {', '.join(sets)} WHERE id = ?",
            vals,
        )
        conn.commit()
        return {"ok": True, "id": signal_id, "updated": keys}
    finally:
        conn.close()


def load_zct_vwap_summary() -> Dict[str, Any]:
    """汇总：持仓笔数、已结算、胜负、累计虚拟盈亏 USDT。"""
    conn = init_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                COUNT(*) AS total_rows,
                SUM(CASE WHEN outcome IS NULL AND sl_price IS NOT NULL
                          AND side IN ('LONG','SHORT') THEN 1 ELSE 0 END) AS open_positions,
                SUM(CASE WHEN outcome IS NOT NULL THEN 1 ELSE 0 END) AS settled_count,
                SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN outcome = 'loss' THEN 1 ELSE 0 END) AS losses,
                SUM(CASE WHEN outcome = 'expired' THEN 1 ELSE 0 END) AS expired_count,
                SUM(CASE WHEN pnl_usdt IS NOT NULL THEN pnl_usdt ELSE 0 END) AS total_pnl_usdt
            FROM zct_vwap_signals
            """
        )
        one = cur.fetchone()
        keys = [d[0] for d in cur.description]
        raw = dict(zip(keys, one))
        settled = int(raw.get("settled_count") or 0)
        wins = int(raw.get("wins") or 0)
        losses = int(raw.get("losses") or 0)
        denom = wins + losses
        win_rate_vs_sl = (wins / denom) if denom else None
        return {
            "ok": True,
            "total_rows": int(raw.get("total_rows") or 0),
            "open_positions": int(raw.get("open_positions") or 0),
            "settled_count": settled,
            "wins": wins,
            "losses": losses,
            "expired_count": int(raw.get("expired_count") or 0),
            "total_pnl_usdt": round(float(raw.get("total_pnl_usdt") or 0), 4),
            "win_rate_closed": round(win_rate_vs_sl, 4) if win_rate_vs_sl is not None else None,
            "note": "虚拟名义按每笔 virtual_notional_usdt；盈亏为纸面结算值。",
        }
    finally:
        conn.close()
