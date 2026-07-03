#!/usr/bin/env python3
"""King Keltner 按美东小时统计平仓胜率与盈亏（7 标池）。"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi

load_env_oi()

import pandas as pd  # noqa: E402

from orb.core.config import OrbConfig  # noqa: E402
from orb.core.kline_cache import load_klines, norm_symbol, session_dates_from_cache  # noqa: E402
from orb.core.symbols import parse_symbol_list  # noqa: E402
from orb.cta.engine import run_cta_backtest  # noqa: E402
from orb.cta.registry import CTA_STRATEGIES, cta_config_for_strategy  # noqa: E402
from orb.kk.paths import resolve_symbols_path  # noqa: E402
from tools.cta.research_vnpy_cta import _session_slice  # noqa: E402

KK = dict(
    compound=True,
    rth_only=True,
    eod_flat=True,
    exit_hour=15,
    exit_minute=55,
    slip_bps_entry=5.0,
    slip_bps_exit=5.0,
    max_notional_usdt=0.0,
)
LO, HI = "2026-02-01", "2026-06-30"


def main() -> None:
    cfg = OrbConfig.from_env()
    meta = CTA_STRATEGIES["king_keltner"]
    symbols = [norm_symbol(s) for s in parse_symbol_list(Path(resolve_symbols_path()).read_text(encoding="utf-8"))]

    by_hour: dict[int, list[float]] = defaultdict(list)
    by_slot: dict[str, list[float]] = defaultdict(list)
    total_closes = 0

    for sym in symbols:
        dates = [d for d in session_dates_from_cache(sym, cfg) if LO <= d <= HI]
        df1 = load_klines(sym, "1m")
        if df1.empty or not dates:
            continue
        chunks = [_session_slice(df1, d, cfg) for d in dates]
        df = pd.concat([c for c in chunks if not c.empty], ignore_index=True)
        if df.empty:
            continue
        out = run_cta_backtest(
            df,
            strategy_fn=meta["fn"],
            orb_cfg=cfg,
            cta_cfg=cta_config_for_strategy("king_keltner", equity_usdt=14, risk_pct=0.01, **KK),
            warmup=25,
        )
        for t in out["trades"]:
            if t["event"] != "close":
                continue
            ms = int(t["ms"])
            ts = pd.Timestamp(ms, unit="ms", tz=cfg.session_tz)
            h = int(ts.hour)
            m = int(ts.minute)
            pnl = float(t["pnl_usdt"])
            by_hour[h].append(pnl)
            total_closes += 1
            if h < 9 or (h == 9 and m < 30):
                continue
            mins = (h - 9) * 60 + m - 30
            if mins < 60:
                by_slot["open_0930_1030"].append(pnl)
            elif mins < 150:
                by_slot["mid_1030_1200"].append(pnl)
            elif mins < 270:
                by_slot["lunch_1200_1400"].append(pnl)
            elif mins < 385:
                by_slot["close_1400_1555"].append(pnl)
            else:
                by_slot["eod_1555_1600"].append(pnl)

    print(f"=== KK hourly edge | {LO}..{HI} | 7 symbols | equity=14U | RTH+EOD ===")
    print(f"total closes={total_closes}\n")

    print("--- 按美东小时 (平仓时刻) ---")
    print(f"{'hour ET':>8s}  {'北京':>8s}  {'n':>5s}  {'win%':>6s}  {'avg U':>8s}  {'sum U':>9s}  tag")
    cn_off = 12  # EDT: ET + 12h = CN (Jul)
    rows = []
    for h in range(9, 17):
        pnls = by_hour.get(h, [])
        if not pnls:
            continue
        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        wr = 100.0 * wins / n
        avg = sum(pnls) / n
        tot = sum(pnls)
        cn_h = (h + cn_off) % 24
        tag = "STRONG" if wr >= 52 and tot > 0 else ("WEAK" if wr < 45 or tot < 0 else "NEUTRAL")
        rows.append((tot, h, cn_h, n, wr, avg, tot, tag))
        print(f"{h:02d}:00 ET  {cn_h:02d}:00 CN  {n:5d}  {wr:5.1f}%  {avg:+8.4f}  {tot:+9.2f}  {tag}")

    print("\n--- 按 RTH 时段块 ---")
    slots = [
        ("open_0930_1030", "09:30-10:30 ET", "21:30-22:30 北京", "开盘突破主段"),
        ("mid_1030_1200", "10:30-12:00 ET", "22:30-00:00 北京", "上午延续"),
        ("lunch_1200_1400", "12:00-14:00 ET", "00:00-02:00 北京", "午间/午后初"),
        ("close_1400_1555", "14:00-15:55 ET", "02:00-03:55 北京", "尾盘震荡"),
        ("eod_1555_1600", "15:55-16:00 ET", "03:55-04:00 北京", "EOD 强平"),
    ]
    for key, et_label, cn_label, note in slots:
        pnls = by_slot.get(key, [])
        if not pnls:
            print(f"{et_label:16s} {cn_label:16s}  n=0")
            continue
        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        wr = 100.0 * wins / n
        tot = sum(pnls)
        avg = tot / n
        tag = "★" if tot > 0 and wr >= 48 else ("✗" if tot < 0 else "~")
        print(f"{tag} {et_label:16s} {cn_label:16s}  n={n:4d}  win={wr:4.1f}%  sum={tot:+8.1f}U  avg={avg:+.4f}  ({note})")

    best = max(rows, key=lambda x: x[0]) if rows else None
    worst = min(rows, key=lambda x: x[0]) if rows else None
    if best:
        print(f"\n最佳小时: {best[1]:02d}:00 ET / {best[2]:02d}:00 北京  sum={best[6]:+.2f}U win={best[4]:.1f}%")
    if worst:
        print(f"最差小时: {worst[1]:02d}:00 ET / {worst[2]:02d}:00 北京  sum={worst[6]:+.2f}U win={worst[4]:.1f}%")


if __name__ == "__main__":
    main()
