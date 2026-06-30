#!/usr/bin/env python3
"""统计：成交单用的是第几个 FVG（相对 5m 确认后）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi
from orb.core.breakout_score import breakout_kline_range_ms
from orb.core.config import OrbConfig
from orb.core.fvg import (
    find_limit_fill,
    first_or_reclaim_bar_ms,
    fvg_min_gap_pct,
    or_end_ms_for_session,
    prox_entry_for_zone,
    scan_first_fvg,
)
from orb.core.kline_cache import load_klines
from orb.core.or_entry_fill import order_deadline_for_signal
from orb.core.session import compute_opening_range, session_anchor_ms, session_close_ms, session_slice
from tools.orb.ml.eval_live_gate import _ml_cfg


def fvg_index_used(
    *,
    side: str,
    or_high: float,
    or_low: float,
    entry_bo: int,
    used_form_ms: int,
    df1,
    df5,
    scan_ms: int,
    close_ms: int,
    bar: int,
    cfg: OrbConfig,
) -> Optional[int]:
    anchor = session_anchor_ms(int(scan_ms), tz=cfg.session_tz, session_open_time=cfg.session_open_time)
    or_end_ms = or_end_ms_for_session(anchor_ms=anchor, cfg=cfg)
    confirm_close_ms = int(entry_bo) + int(bar)
    deadline = order_deadline_for_signal(scan_ms=scan_ms, cfg=cfg, session_close_ms=int(close_ms))
    min_gap = fvg_min_gap_pct(cfg)
    side_u = str(side).upper()

    cursor = int(confirm_close_ms)
    last_reclaim_ms = -1
    idx = 0
    while cursor < deadline:
        reclaim = first_or_reclaim_bar_ms(
            df5, after_ms=confirm_close_ms, before_ms=cursor, or_high=or_high, or_low=or_low
        )
        if reclaim is not None and int(reclaim) > last_reclaim_ms:
            last_reclaim_ms = int(reclaim)
            cursor = int(reclaim) + int(bar)
            continue
        zone = scan_first_fvg(
            df1,
            side=side_u,
            after_ms=cursor,
            before_ms=deadline,
            or_end_ms=or_end_ms,
            min_gap_pct=min_gap,
        )
        if zone is None:
            return None
        idx += 1
        if int(zone.form_bar_open_ms) == int(used_form_ms):
            return idx
        entry_px = prox_entry_for_zone(zone)
        fill_after = int(zone.form_bar_open_ms) + 60_000
        hit = find_limit_fill(
            df1, side=side_u, entry_px=entry_px, after_ms=fill_after, before_ms=deadline
        )
        if hit is not None and int(zone.form_bar_open_ms) != int(used_form_ms):
            # 理论上不应发生：在更早 FVG 成交却记录了更晚 form_ms
            pass
        cursor = int(zone.form_bar_open_ms) + 60_000
    return None


def main() -> int:
    load_env_oi()
    cfg = _ml_cfg(compound_per_symbol=True)
    bar = cfg.bar_step_ms()
    tz = cfg.session_tz

    jpath = ROOT / "output/orb/v2/eval/live_sim_2026-06_fvg_prox_eq14.json"
    trades = [t for d in json.loads(jpath.read_text(encoding="utf-8"))["days"] for t in d.get("trades") or []]

    k5: Dict[tuple, Any] = {}
    k1: Dict[tuple, Any] = {}

    rows: List[Dict[str, Any]] = []
    missed: List[Dict[str, Any]] = []

    for t in trades:
        sym = str(t["symbol"])
        day = str(t["session_date"])
        key = (sym, day)
        scan_ms = int(t["scan_open_ms"])
        used_form = int(t.get("fvg_form_ms") or 0)
        if used_form <= 0:
            continue

        if key not in k5:
            fs, end_ms = breakout_kline_range_ms(day, cfg)
            k5[key] = load_klines(sym, cfg.signal_interval, start_ms=fs, end_ms=end_ms)
            k1[key] = load_klines(sym, "1m", start_ms=fs, end_ms=end_ms)

        ts = __import__("pandas").Timestamp(f"{day} 12:00:00", tz=tz)
        anchor = session_anchor_ms(int(ts.value // 1_000_000), tz=tz, session_open_time=cfg.session_open_time)
        close = session_close_ms(anchor, tz=tz, session_close_time=cfg.session_close_time) or anchor + 6 * 3600_000

        sess = session_slice(k5[key], scan_ms, tz=cfg.session_tz, session_open_time=cfg.session_open_time)
        pack = compute_opening_range(
            sess,
            or_minutes=cfg.or_minutes,
            bar_step_ms=bar,
            asof_open_ms=scan_ms,
            tz=cfg.session_tz,
            session_open_time=cfg.session_open_time,
        )
        if pack is None:
            continue
        or_high = float(pack["or_high"])
        or_low = float(pack["or_low"])

        fvg_idx = None
        for entry_bo in (scan_ms, scan_ms - bar, scan_ms - 2 * bar):
            fvg_idx = fvg_index_used(
                side=str(t["side"]),
                or_high=or_high,
                or_low=or_low,
                entry_bo=int(entry_bo),
                used_form_ms=used_form,
                df1=k1[key],
                df5=k5[key],
                scan_ms=scan_ms,
                close_ms=close,
                bar=bar,
                cfg=cfg,
            )
            if fvg_idx is not None:
                break
        if fvg_idx is None:
            continue

        row = {
            "date": day,
            "scan_et": t.get("scan_et"),
            "symbol": sym.replace("USDT", ""),
            "side": t.get("side"),
            "pnl": float(t.get("pnl_usdt") or 0),
            "fvg_idx": fvg_idx,
        }
        rows.append(row)
        if fvg_idx >= 2:
            missed.append(row)

    print(f"traced: {len(rows)} / {len(trades)}")
    print(f"FVG #1: {sum(1 for r in rows if r['fvg_idx'] == 1)}")
    print(f"FVG #2+: {len(missed)}")
    print()

    if not missed:
        print("没有「跳过第一个 FVG、用更后面 FVG 成交」的笔数（在可回放样本内）。")
        return 0

    by_sym: Dict[str, Dict[str, float]] = {}
    for r in missed:
        s = r["symbol"]
        by_sym.setdefault(s, {"n": 0, "pnl": 0.0})
        by_sym[s]["n"] += 1
        by_sym[s]["pnl"] += r["pnl"]

    print("FVG #2+ 成交（按标的）:")
    for sym, s in sorted(by_sym.items(), key=lambda x: x[1]["pnl"]):
        print(f"  {sym:8s} {int(s['n']):2d}笔  net {s['pnl']:+.2f}U")

    print("\n明细:")
    for r in sorted(missed, key=lambda x: (x["date"], x["symbol"])):
        print(f"  {r['date']} {r['scan_et']} {r['symbol']:6s} {r['side']:<5} FVG#{r['fvg_idx']} pnl={r['pnl']:+.2f}U")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
