#!/usr/bin/env python3
"""
Phase 2：日内滚动淘汰 — 仅检查当前触轨池标的，不跑全市场回测。

规则（默认）：自当前 UTC 自然日 0:00（= 上海 08:00 VWAP 会话起点）起，
按 settlements 时间序，若某标的末段 **连续 3 笔 outcome=loss**，从 touch_pool DELETE。

池子当日只减不增（主筛仅在 08:05 重写全表）。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from accumulation_radar import init_db
from zct_db_repositories import settlements_table_ident
from zct_vwap_touch_pool_db import (
    touch_pool_list_symbols,
    touch_pool_physical_table_names,
    touch_pool_prune_signals_vs_allowlist,
    touch_pool_remove_symbols,
)

logger = logging.getLogger(__name__)


def current_utc_session_start_iso() -> str:
    """当前 UTC 自然日 0:00（与 Session VWAP 日切一致）。"""
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.strftime("%Y-%m-%d %H:%M:%S")


def trailing_loss_streak_from_settlements(rows: List[tuple]) -> int:
    """rows: (outcome,) 按 settled_at 升序。"""
    streak = 0
    for (outcome,) in rows:
        oc = str(outcome or "").lower()
        if oc == "loss":
            streak += 1
        elif oc == "win":
            streak = 0
    return streak


def run_intraday_prune(
    *,
    min_consecutive_losses: int = 3,
    session_start_utc: Optional[str] = None,
) -> Dict[str, Any]:
    """
    检查池内标的；达连续止损阈值则从 touch_pool 删除（不 commit 由调用方处理）。
    返回 {removed, checked, details}.
    """
    threshold = max(1, int(min_consecutive_losses))
    since = session_start_utc or current_utc_session_start_iso()
    settle_tbl = settlements_table_ident()

    conn = init_db()
    try:
        pool_syms = touch_pool_list_symbols(conn)
        if not pool_syms:
            return {"removed": [], "checked": 0, "since_utc": since}

        cur = conn.cursor()
        to_remove: List[str] = []
        details: List[Dict[str, Any]] = []

        for sym in pool_syms:
            cur.execute(
                f"""
                SELECT outcome FROM {settle_tbl}
                WHERE symbol = ? AND settled_at_utc >= ?
                  AND outcome IN ('win', 'loss')
                ORDER BY settled_at_utc ASC, id ASC
                """,
                (sym, since),
            )
            rows = cur.fetchall()
            streak = trailing_loss_streak_from_settlements(rows)
            if streak >= threshold:
                to_remove.append(sym)
                details.append(
                    {
                        "symbol": sym,
                        "trailing_loss_streak": streak,
                        "settlements_since_session": len(rows),
                    }
                )

        removed: List[str] = []
        signals_pruned = 0
        if to_remove:
            n = touch_pool_remove_symbols(conn, to_remove)
            removed = list(to_remove)
            remaining = [s for s in pool_syms if s not in set(removed)]
            signals_pruned = touch_pool_prune_signals_vs_allowlist(conn, remaining)
            conn.commit()
            pt, _ = touch_pool_physical_table_names()
            logger.info(
                "intraday_prune removed=%s pool_rows=%s signals_pruned=%s since=%s",
                removed,
                n,
                signals_pruned,
                since,
            )
        else:
            conn.commit()

        return {
            "removed": removed,
            "checked": len(pool_syms),
            "since_utc": since,
            "threshold": threshold,
            "signals_pruned": signals_pruned,
            "details": details,
        }
    finally:
        conn.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    ap = argparse.ArgumentParser(description="ZCT 触轨池日内淘汰")
    ap.add_argument(
        "--min-consecutive-losses",
        type=int,
        default=int(os.getenv("ZCT_TOUCH_POOL_INTRADAY_LOSS_STREAK", "3") or 3),
        help="末段连续止损达到该值则出池",
    )
    args = ap.parse_args()
    out = run_intraday_prune(min_consecutive_losses=int(args.min_consecutive_losses))
    if out.get("removed"):
        print(f"[intraday_prune] removed={out['removed']}", flush=True)
    else:
        print(
            f"[intraday_prune] ok checked={out.get('checked')} since={out.get('since_utc')}",
            flush=True,
        )


if __name__ == "__main__":
    main()
