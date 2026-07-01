#!/usr/bin/env python3
"""COIN king_keltner 回测明细。"""
from __future__ import annotations

import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pandas as pd

from env_loader import load_env_oi
from orb.core.config import OrbConfig
from orb.core.kline_cache import load_klines, norm_symbol
from orb.core.session import session_day_str
from orb.cta.engine import run_cta_backtest
from orb.cta.registry import CTA_STRATEGIES, cta_config_for_strategy
from tools.cta.research_vnpy_cta import _session_slice
from orb.core.kline_cache import session_dates_from_cache


def main() -> None:
    load_env_oi()
    cfg = OrbConfig.from_env()
    sym = norm_symbol("COIN")
    dates = [d for d in session_dates_from_cache(sym, cfg) if "2026-02-09" <= d <= "2026-06-30"]
    df1 = load_klines(sym, "1m")
    chunks = [_session_slice(df1, d, cfg) for d in dates]
    df = pd.concat([c for c in chunks if not c.empty], ignore_index=True).sort_values("open_time")

    meta = CTA_STRATEGIES["king_keltner"]
    out = run_cta_backtest(
        df,
        strategy_fn=meta["fn"],
        orb_cfg=cfg,
        cta_cfg=cta_config_for_strategy(
            "king_keltner",
            equity_usdt=1000,
            risk_pct=0.01,
            compound=True,
            maker_bps=2,
            taker_bps=4,
        ),
        warmup=int(meta.get("warmup") or 25),
    )

    opens = [t for t in out["trades"] if t["event"] == "open"]
    closes = [t for t in out["trades"] if t["event"] == "close"]
    pairs = []
    oi = 0
    wallet = 1000.0
    for c in closes:
        while oi < len(opens) and opens[oi]["ms"] > c["ms"]:
            oi += 1
        if oi >= len(opens):
            break
        o = opens[oi]
        oi += 1
        dur_min = (int(c["ms"]) - int(o["ms"])) / 60_000
        day = session_day_str(int(o["ms"]), tz=cfg.session_tz, session_open_time=cfg.session_open_time)
        wallet_after = round(wallet + float(c["pnl_usdt"]), 4)
        pairs.append(
            {
                "session_date": day,
                "side": c["side"],
                "entry": o["entry"],
                "exit": c["exit"],
                "sl": o["sl"],
                "notional_usdt": o["notional_usdt"],
                "pnl_usdt_gross": c["pnl_usdt_gross"],
                "fee_usdt": c["fee_usdt"],
                "pnl_usdt": c["pnl_usdt"],
                "outcome": c["outcome"],
                "hold_minutes": round(dur_min, 1),
                "wallet_after": wallet_after,
                "entry_ms": o["ms"],
            }
        )
        wallet = wallet_after

    out_dir = ROOT / "output" / "orb" / "cta"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "COIN_king_keltner_eq1000_trades.csv"
    fields = list(pairs[0].keys()) if pairs else []
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(pairs)

    s = out["summary"]
    oc = Counter(p["outcome"] for p in pairs)
    side_c = Counter(p["side"] for p in pairs)
    wins = [p for p in pairs if float(p["pnl_usdt"]) > 0]
    losses = [p for p in pairs if float(p["pnl_usdt"]) < 0]
    gross = sum(float(p["pnl_usdt_gross"]) for p in pairs)
    fees = sum(float(p["fee_usdt"]) for p in pairs)
    net = sum(float(p["pnl_usdt"]) for p in pairs)

    print("=== COIN king_keltner | 1000U | 1% risk | compound ON | 8bps RT ===")
    print(f"sessions {dates[0]} .. {dates[-1]} ({len(dates)} days)")
    print(f"opens={s['opens']} closes={len(pairs)}")
    print(f"gross={gross:+.2f}U  fees={fees:.2f}U  net={net:+.2f}U  equity_end={s['equity_end']}")
    print(f"win={len(wins)} loss={len(losses)} win_rate={100*len(wins)/len(pairs):.1f}%")
    print("outcomes", dict(oc))
    print("sides", dict(side_c))
    if wins:
        print(f"avg win={sum(float(p['pnl_usdt']) for p in wins)/len(wins):+.2f}U")
    if losses:
        print(f"avg loss={sum(float(p['pnl_usdt']) for p in losses)/len(losses):+.2f}U")
    print(f"avg hold={sum(p['hold_minutes'] for p in pairs)/len(pairs):.0f} min")
    print(f"notional avg={sum(float(p['notional_usdt']) for p in pairs)/len(pairs):.0f} min={min(float(p['notional_usdt']) for p in pairs):.0f} max={max(float(p['notional_usdt']) for p in pairs):.0f}")

    by_day: dict = defaultdict(lambda: {"n": 0, "net": 0.0, "fees": 0.0})
    for p in pairs:
        by_day[p["session_date"]]["n"] += 1
        by_day[p["session_date"]]["net"] += float(p["pnl_usdt"])
        by_day[p["session_date"]]["fees"] += float(p["fee_usdt"])

    print("\n--- Top 10 winning days ---")
    for d, v in sorted(by_day.items(), key=lambda x: -x[1]["net"])[:10]:
        print(f"  {d}  trades={v['n']:2d}  net={v['net']:+.2f}U  fees={v['fees']:.2f}")

    print("\n--- Worst 10 days ---")
    for d, v in sorted(by_day.items(), key=lambda x: x[1]["net"])[:10]:
        print(f"  {d}  trades={v['n']:2d}  net={v['net']:+.2f}U  fees={v['fees']:.2f}")

    print("\n--- Top 10 trades ---")
    for p in sorted(pairs, key=lambda x: -float(x["pnl_usdt"]))[:10]:
        print(
            f"  {p['session_date']} {p['side']:5s} {float(p['pnl_usdt']):+7.2f}U "
            f"notional={float(p['notional_usdt']):6.0f} hold={p['hold_minutes']:4.0f}m {p['outcome']}"
        )

    print("\n--- Worst 10 trades ---")
    for p in sorted(pairs, key=lambda x: float(x["pnl_usdt"]))[:10]:
        print(
            f"  {p['session_date']} {p['side']:5s} {float(p['pnl_usdt']):+7.2f}U "
            f"notional={float(p['notional_usdt']):6.0f} hold={p['hold_minutes']:4.0f}m {p['outcome']}"
        )

    # monthly
    by_m: dict = defaultdict(lambda: {"n": 0, "net": 0.0})
    for p in pairs:
        m = p["session_date"][:7]
        by_m[m]["n"] += 1
        by_m[m]["net"] += float(p["pnl_usdt"])
    print("\n--- Monthly ---")
    for m in sorted(by_m):
        v = by_m[m]
        print(f"  {m}  trades={v['n']:3d}  net={v['net']:+.2f}U")

    print(f"\ncsv -> {csv_path}")


if __name__ == "__main__":
    main()
