#!/usr/bin/env python3
"""Quick stats on gtl flip sim output."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "output" / "orb" / "cta"


def main() -> int:
    legs = list(csv.DictReader((OUT / "gtl_flip_2026-06-25_2026-07-03_legs.csv").open()))
    summ = list(csv.DictReader((OUT / "gtl_flip_2026-06-25_2026-07-03.csv").open()))
    recent = json.loads((OUT / "gtl_pool7_recent_2026-06-25_2026-07-03_5m.json").read_text())

    by_reason: dict[str, dict] = defaultdict(lambda: {"n": 0, "pnl": 0.0, "pct": []})
    for lg in legs:
        r = lg["reason"]
        by_reason[r]["n"] += 1
        by_reason[r]["pnl"] += float(lg["pnl"])
        by_reason[r]["pct"].append(float(lg["pct"]))

    print("=== Exit reason stats ===")
    for r, v in sorted(by_reason.items(), key=lambda x: -x[1]["n"]):
        avg = sum(v["pct"]) / len(v["pct"])
        print(f"{r:12s} n={v['n']:2d}  sum_pnl={v['pnl']:+8.2f}  avg_pct={avg:+.2f}%")

    leg_idx: dict[tuple[str, str], list] = defaultdict(list)
    for lg in legs:
        leg_idx[(lg["day"], lg["symbol"])].append(lg)
    l1 = l2 = 0
    p1 = p2 = 0.0
    c1 = c2 = 0.0
    for arr in leg_idx.values():
        if arr:
            l1 += 1
            p1 += float(arr[0]["pnl"])
            c1 += float(arr[0]["pct"])
        if len(arr) >= 2:
            l2 += 1
            p2 += float(arr[1]["pnl"])
            c2 += float(arr[1]["pct"])
    print("\n=== Leg order ===")
    print(f"Leg1: n={l1} sum={p1:+.2f} avg_pct={c1/l1:+.2f}%")
    print(f"Leg2: n={l2} sum={p2:+.2f} avg_pct={c2/l2:+.2f}%")

    break_days: list[float] = []
    fc_days: list[float] = []
    for row in summ:
        if row.get("skipped") == "True":
            continue
        rec = next(
            (x for x in recent["results"] if x["symbol"] == row["symbol"] and x["day"] == row["day"]),
            None,
        )
        if not rec:
            continue
        if rec.get("open_break"):
            break_days.append(float(row["total_pnl"]))
        else:
            fc_days.append(float(row["total_pnl"]))
    print("\n=== Open signal type (day total) ===")
    print(
        f"open_break @09:30: n={len(break_days)} sum={sum(break_days):+.2f} "
        f"avg={sum(break_days)/len(break_days):+.2f}"
    )
    print(
        f"forecast-only open: n={len(fc_days)} sum={sum(fc_days):+.2f} "
        f"avg={sum(fc_days)/len(fc_days):+.2f}"
    )

    conf: list[tuple] = []
    agree: list[float] = []
    for rec in recent["results"]:
        ob = rec.get("open_break", "")
        of = rec.get("open_forecast", "")
        if not ob:
            continue
        row = next(
            (
                x
                for x in summ
                if x["symbol"] == rec["symbol"] and x["day"] == rec["day"] and x.get("skipped") != "True"
            ),
            None,
        )
        if not row:
            continue
        pnl = float(row["total_pnl"])
        if of and ob != of:
            conf.append((rec["symbol"], rec["day"], ob, of, pnl))
        else:
            agree.append(pnl)
    print("\n=== open_break vs forecast conflict ===")
    print(f"agree/missing_fc: n={len(agree)} sum={sum(agree):+.2f}")
    for t in conf:
        print(f"  {t[0]} {t[1]} break={t[2]} fc={t[3]} pnl={t[4]:+.2f}")

    early_sl = late_sl = 0
    es_p = ls_p = 0.0
    for lg in legs:
        if lg["reason"] != "sl":
            continue
        eh, em = map(int, lg["entry_time"].split(":"))
        xh, xm = map(int, lg["exit_time"].split(":"))
        mins = (xh * 60 + xm) - (eh * 60 + em)
        if mins <= 25:
            early_sl += 1
            es_p += float(lg["pnl"])
        else:
            late_sl += 1
            ls_p += float(lg["pnl"])
    print("\n=== SL timing ===")
    print(f"<=25min: n={early_sl} sum={es_p:+.2f}")
    print(f">25min:  n={late_sl} sum={ls_p:+.2f}")

    sym_pnl: dict[str, float] = defaultdict(float)
    for row in summ:
        if row.get("skipped") == "True":
            continue
        sym_pnl[row["symbol"]] += float(row["total_pnl"])
    print("\n=== 7d by symbol ===")
    for s, v in sorted(sym_pnl.items(), key=lambda x: -x[1]):
        print(f"{s:5s} {v:+8.2f}")

    one = two = 0
    o_p = t_p = 0.0
    for row in summ:
        if row.get("skipped") == "True" or row["symbol"] == "SNDK":
            continue
        n = int(row["n_legs"] or 0)
        p = float(row["total_pnl"])
        if n == 1:
            one += 1
            o_p += p
        elif n == 2:
            two += 1
            t_p += p
    print("\n=== Leg count days ex SNDK ===")
    print(f"1 leg: n={one} sum={o_p:+.2f} avg={o_p/one:+.2f}")
    print(f"2 leg: n={two} sum={t_p:+.2f} avg={t_p/two:+.2f}")

    eod_only = [lg for lg in legs if lg["reason"] == "eod"]
    print(f"\n=== EOD exits n={len(eod_only)} sum={sum(float(x['pnl']) for x in eod_only):+.2f} "
          f"avg_pct={sum(float(x['pct']) for x in eod_only)/len(eod_only):+.2f}%")

    flip = [lg for lg in legs if "flip" in lg["reason"]]
    print(f"=== Flip exits n={len(flip)} sum={sum(float(x['pnl']) for x in flip):+.2f}")
    for lg in flip:
        print(f"  {lg['day']} {lg['symbol']} {lg['side']} {lg['pnl']} pct={lg['pct']} {lg['reason']}")

    # second leg after SL on first
    sl_then = []
    for arr in leg_idx.values():
        if len(arr) >= 2 and arr[0]["reason"] == "sl":
            sl_then.append(float(arr[0]["pnl"]) + float(arr[1]["pnl"]))
    print(f"\n=== Days: leg1 SL then leg2 === n={len(sl_then)} sum={sum(sl_then):+.2f} avg={sum(sl_then)/len(sl_then):+.2f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
