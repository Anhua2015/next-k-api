#!/usr/bin/env python3
"""Explain gap between GTL direction accuracy and trading PnL."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "output" / "orb" / "cta"


def leg_stats(legs: list[dict], label: str) -> None:
    n = len(legs)
    wins = sum(1 for r in legs if float(r["pnl"]) > 0)
    print(f"\n{label} leg win rate: {wins}/{n} = {100 * wins / n:.1f}%")
    print(f"  avg pct/leg: {sum(float(r['pct']) for r in legs) / n:+.2f}%")
    by_reason: dict[str, list[dict]] = defaultdict(list)
    for r in legs:
        by_reason[r["reason"]].append(r)
    for reason, arr in sorted(by_reason.items()):
        w = sum(1 for x in arr if float(x["pnl"]) > 0)
        print(
            f"  {reason:12s} n={len(arr):2d} win={100 * w / len(arr):4.0f}% "
            f"sum={sum(float(x['pnl']) for x in arr):+8.2f} "
            f"avg_pct={sum(float(x['pct']) for x in arr) / len(arr):+.2f}%"
        )


def day_stats(rows: list[dict], label: str) -> None:
    traded = [r for r in rows if r.get("skipped") != "True"]
    wins = sum(1 for r in traded if float(r["total_pnl"]) > 0)
    n = len(traded)
    print(f"{label} day win rate: {wins}/{n} = {100 * wins / n:.1f}%")


def main() -> int:
    detail = list(csv.DictReader((OUT / "gtl_pool7_detail_2026-06-25_2026-07-03_5m.csv").open()))
    n = len(detail)
    print("=== A. aligned break 事件（detail CSV，325 个 intraday break）===")
    print("定义：在 break 入场价，看 N 根 5m 后价格是否沿 break 方向走")
    for h in ("1", "4", "20"):
        ok = sum(1 for r in detail if r[f"ok_{h}"] == "+")
        print(f"  forward ok_{h} ({int(h)*5:3d}min): {ok}/{n} = {100 * ok / n:.1f}%")
    sl20 = sum(1 for r in detail if r["stop_hit_20"] == "Y")
    print(f"  但 20bar 内先触结构止损: {sl20}/{n} = {100 * sl20 / n:.1f}%")

    recent = json.loads((OUT / "gtl_pool7_recent_2026-06-25_2026-07-03_5m.json").read_text())
    hits = [r["birth_hit_rate"] for r in recent["results"] if r.get("birth_hit_rate") is not None]
    print(f"\n=== B. birth_hit_rate（sym-day 均值）===")
    print(f"  定义：当日 aligned break 中，后续 1bar 沿 break 方向的比例")
    print(f"  avg = {100 * sum(hits) / len(hits):.1f}%  (n={len(hits)} sym-days)")

    legs_a = list(csv.DictReader((OUT / "gtl_flip_2026-06-25_2026-07-03_legs.csv").open()))
    legs_b = list(csv.DictReader((OUT / "gtl_flip_slflat_2026-06-25_2026-07-03_legs.csv").open()))
    print("\n=== C. 实际交易 leg 胜率（你的策略进出点）===")
    leg_stats(legs_a, "A 原版 flip")
    leg_stats(legs_b, "B SL-flat flip (ex partial SNDK run)")

    rows_a = list(csv.DictReader((OUT / "gtl_flip_2026-06-25_2026-07-03.csv").open()))
    rows_b = list(csv.DictReader((OUT / "gtl_flip_slflat_2026-06-25_2026-07-03.csv").open()))
    print("\n=== D. sym-day 合计胜率 ===")
    day_stats(rows_a, "A 原版")
    day_stats(rows_b, "B sl-flat")

    # open leg only (leg1 per day)
    leg_idx: dict[tuple[str, str], list] = defaultdict(list)
    for r in legs_a:
        leg_idx[(r["day"], r["symbol"])].append(r)
    leg1 = [arr[0] for arr in leg_idx.values() if arr]
    print("\n=== E. 仅第 1 腿（09:30 开仓）===")
    leg_stats(leg1, "A 原版 leg1")

    # direction correct at EOD but SL first?
    print("\n=== F. 为何方向对但交易亏 ===")
    print("  1) 研究统计：break 时刻入场 → 看 forward move")
    print("  2) 实际交易：09:30 开盘入场 → 结构止损可能先被打")
    print("  3) 325 break 里 ok_20=高，但 stop_hit_20 也不低 → 方向对但中途止损")
    sl = [r for r in legs_a if r["reason"] == "sl"]
    eod = [r for r in legs_a if r["reason"] == "eod"]
    leg2 = [arr[1] for arr in leg_idx.values() if len(arr) >= 2]
    print(f"  SL 腿: n={len(sl)} 均 pct={sum(float(r['pct']) for r in sl)/len(sl):+.2f}%")
    print(f"  EOD腿: n={len(eod)} 均 pct={sum(float(r['pct']) for r in eod)/len(eod):+.2f}%")
    print(f"  第2腿: n={len(leg2)} win={100*sum(1 for r in leg2 if float(r['pnl'])>0)/len(leg2):.0f}% sum={sum(float(r['pnl']) for r in leg2):+.2f}")

    # intraday break timing
    print("\n=== G. aligned break 按时段（break 时刻入场 research）===")
    by: dict[str, list] = defaultdict(list)

    def bucket(t: str) -> str:
        h = int(t.split(":")[0])
        if h < 10:
            return "09:30-09:59"
        if h < 12:
            return "10:00-11:59"
        if h < 14:
            return "12:00-13:59"
        return "14:00-15:55"

    for r in detail:
        by[bucket(r["time_et"])].append(r)
    for b in ("09:30-09:59", "10:00-11:59", "12:00-13:59", "14:00-15:55"):
        arr = by.get(b, [])
        if not arr:
            continue
        ok = sum(1 for x in arr if x["ok_20"] == "+")
        sl = sum(1 for x in arr if x["stop_hit_20"] == "Y")
        avg = sum(float(x["pct_20"]) for x in arr) / len(arr)
        print(
            f"  {b:14s} n={len(arr):3d} ok_20={100 * ok / len(arr):4.0f}% "
            f"sl20={100 * sl / len(arr):4.0f}% avg_pct_20={avg:+.2f}%"
        )

    first: dict[tuple[str, str], dict] = {}
    for r in detail:
        k = (r["day"], r["symbol"])
        if k not in first:
            first[k] = r
    fa = list(first.values())
    ok_f = sum(1 for x in fa if x["ok_20"] == "+")
    print(
        f"\n  当日首个 aligned break（任意时刻）: n={len(fa)} "
        f"ok_20={100 * ok_f / len(fa):.0f}% "
        f"avg_pct_20={sum(float(x['pct_20']) for x in fa) / len(fa):+.2f}%"
    )
    open_b = [r for r in detail if r["time_et"] == "09:30"]
    if open_b:
        ok_o = sum(1 for x in open_b if x["ok_20"] == "+")
        print(
            f"  仅 09:30 这根 bar 出 break: n={len(open_b)} "
            f"ok_20={100 * ok_o / len(open_b):.0f}%"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
