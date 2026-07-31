"""Screen desk (+ dig backups) for gentle anti-martingale fitness (30d).

Fitness metrics (what AM likes):
  - green_day_rate: fraction of days with paper day_ret > 0
  - baseline_mdd: lower is better
  - am_edge = am_ret - base_ret (must be > 0)
  - am_mdd: keep small
  - score = am_edge - 0.5*am_mdd  (simple)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts._bt_anti_martingale_gentle_30d import run  # noqa: E402
from scripts.backtest_ht7_30d import av_series_month, fetch_fills  # noqa: E402
from utils.hl_bitget_symbol_map import bitget_contract_set  # noqa: E402
from utils.hl_short_term import load_watchlist  # noqa: E402

BJ = timezone(timedelta(hours=8))
OUT = ROOT / "hl_ht_true" / "anti_martingale_screen_30d.json"

# Extra dig names that looked AM-shaped (steady / moderate MDD)
EXTRA = {
    "dig_2bfb": "0x2bfb05f4843c471a2be37924272ed2a5582bda24",
    "dig_f206": "0xf2060700d2f18e5727205652a181fcf970e4a5ce",  # = bot_i
}


def green_stats(days: list[dict[str, Any]]) -> dict[str, Any]:
    if not days:
        return {"green_rate": None, "red_days": 0, "green_days": 0, "days_n": 0}
    g = sum(1 for d in days if float(d.get("ret_pct") or 0) > 1e-6)
    r = sum(1 for d in days if float(d.get("ret_pct") or 0) < -1e-6)
    return {
        "green_rate": round(g / len(days), 3),
        "green_days": g,
        "red_days": r,
        "days_n": len(days),
    }


def main() -> None:
    bitget_contract_set(force=True)
    now = datetime.now(BJ)
    start_ms = int((now - timedelta(days=30)).timestamp() * 1000)

    pool: list[tuple[str, str]] = []
    seen: set[str] = set()
    for w in load_watchlist():
        bid = str(w.get("id") or "")
        addr = str(w.get("address") or "").lower()
        if not addr or bid == "bot_o":  # retired seat; skip if leftover in old dumps
            continue
        if addr in seen:
            continue
        seen.add(addr)
        pool.append((bid, addr))
    for name, addr in EXTRA.items():
        a = addr.lower()
        if a in seen:
            continue
        seen.add(a)
        pool.append((name, a))

    print(f"screen n={len(pool)}", flush=True)
    rows: list[dict[str, Any]] = []
    for i, (name, addr) in enumerate(pool, 1):
        print(f"[{i}/{len(pool)}] {name} {addr[:12]}…", flush=True)
        try:
            fills = fetch_fills(addr, start_ms)
            avs = av_series_month(addr)
            if len(fills) < 20:
                print(f"  skip thin fills={len(fills)}", flush=True)
                rows.append({"id": name, "addr": addr, "skip": "thin_fills", "fills_n": len(fills)})
                continue
            base = run(fills, avs, addr, "baseline")
            am = run(fills, avs, addr, "gentle")
            gs = green_stats(base["days"])
            edge = round(am["ret_pct"] - base["ret_pct"], 2)
            score = round(edge - 0.5 * am["mdd_pct"], 2)
            row = {
                "id": name,
                "addr": addr,
                "fills_n": len(fills),
                "green_rate": gs["green_rate"],
                "green_days": gs["green_days"],
                "red_days": gs["red_days"],
                "days_n": gs["days_n"],
                "base_ret": base["ret_pct"],
                "base_mdd": base["mdd_pct"],
                "am_ret": am["ret_pct"],
                "am_mdd": am["mdd_pct"],
                "am_edge_pp": edge,
                "am_breaks": am["mdd_breaks"],
                "score": score,
            }
            rows.append(row)
            print(
                f"  green={gs['green_rate']} base={base['ret_pct']}%/{base['mdd_pct']}% "
                f"am={am['ret_pct']}%/{am['mdd_pct']}% edge={edge} score={score}",
                flush=True,
            )
        except Exception as exc:
            print(f"  FAIL {exc}", flush=True)
            rows.append({"id": name, "addr": addr, "skip": str(exc)})

    ranked = sorted(
        [r for r in rows if "am_ret" in r],
        key=lambda r: (r.get("score") is not None, r.get("score") or -1e9),
        reverse=True,
    )
    out = {
        "generated_at": now.isoformat(),
        "window_days": 30,
        "metric": {
            "green_rate": "baseline paper days with ret>0",
            "am_edge_pp": "gentle_AM ret - baseline ret",
            "score": "am_edge_pp - 0.5 * am_mdd",
            "am_rule": "green +0.5 cap2; red→1; flat hold; MDD15% break",
        },
        "ranked": ranked,
        "all": rows,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nTOP", flush=True)
    for r in ranked[:8]:
        print(
            f"  {r['id']}: score={r['score']} edge={r['am_edge_pp']} "
            f"am={r['am_ret']}%/{r['am_mdd']}% green={r['green_rate']}",
            flush=True,
        )
    print("wrote", OUT)


if __name__ == "__main__":
    main()
