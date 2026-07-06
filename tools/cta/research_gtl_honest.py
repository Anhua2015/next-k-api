#!/usr/bin/env python3
"""GTL honest research: forecast validation + birth_break honest backtest vs buy&hold."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi  # noqa: E402

load_env_oi()

import pandas as pd  # noqa: E402

from orb.core.config import OrbConfig  # noqa: E402
from orb.core.kline_cache import norm_symbol  # noqa: E402
from orb.gtl.engine import compute_gtl_dataframe  # noqa: E402
from orb.gtl.resample import resample_ohlcv  # noqa: E402
from orb.gtl.vnpy.backtest import run_gtl_vnpy_backtest  # noqa: E402
from tools.cta.research_gtl_vnpy import _load_symbol_df  # noqa: E402
from tools.cta.validate_gtl import _honest_trading_sim, _load  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="GTL honest research bundle")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--from-date", default="2026-03-01")
    ap.add_argument("--to-date", default="2026-07-04")
    ap.add_argument("--resample", default="30m")
    ap.add_argument("--equity", type=float, default=1000.0)
    ap.add_argument("--json-out", default="")
    ap.add_argument("--skip-validate", action="store_true")
    args = ap.parse_args()

    sym = norm_symbol(args.symbol)
    lo, hi = args.from_date.strip(), args.to_date.strip()
    rs = args.resample.strip() or "1m"

    if not args.skip_validate:
        print("[1/3] validate_gtl", flush=True)
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "cta" / "validate_gtl.py"),
                "--symbol",
                sym,
                "--from-date",
                lo,
                "--to-date",
                hi,
                "--resample",
                rs,
            ],
            check=False,
        )
        print()

    cfg = OrbConfig.from_env()
    fetch_lo = (pd.Timestamp(lo) - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    raw = _load_symbol_df(sym, fetch_lo, hi, cfg)
    if raw.empty:
        raw = _load(sym, lo, hi)
    if rs != "1m":
        raw = resample_ohlcv(raw, rs)
    if raw.empty:
        print("no data")
        return 1

    gtl = compute_gtl_dataframe(raw, lookback=23, vol_window=500)
    aligned_n = int(gtl["break_aligns_birth"].sum())
    sim = _honest_trading_sim(raw, gtl)

    start = pd.Timestamp(lo).to_pydatetime()
    end = (pd.Timestamp(hi) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)).to_pydatetime()

    print("[2/3] vnpy backtests", flush=True)
    t0 = time.time()
    results = {}
    for key in ("gtl_birth_break", "gtl_birth_break_honest"):
        r = run_gtl_vnpy_backtest(key, sym, df=raw, start=start, end=end, capital=float(args.equity), quiet=True)
        s = r.get("summary") or {}
        opens = int(s.get("opens") or 0)
        results[key] = {
            **s,
            "aligned_setup_coverage": round(opens / aligned_n, 3) if aligned_n else 0.0,
        }
        print(
            f"  {key:24s} net={float(s.get('net_pnl') or 0):+.2f} "
            f"realized={float(s.get('realized_pnl') or 0):+.2f} "
            f"opens={opens}/{aligned_n} bh={float(s.get('buy_hold_move') or 0):+.2f}",
            flush=True,
        )

    print("\n[3/3] summary", flush=True)
    bh = float(sim.get("buy_hold_move") or 0)
    honest = results.get("gtl_birth_break_honest") or {}
    print(f"  symbol={sym} resample={rs} aligned_setups={aligned_n}")
    print(f"  buy_hold_move          = {bh:+.2f}")
    print(f"  sim hold_20bar (no fee)= {float(sim.get('hold_20_sum') or 0):+.2f}")
    print(f"  honest realized_pnl    = {float(honest.get('realized_pnl') or 0):+.2f}")
    print(f"  honest vs buy_hold     = {float(honest.get('realized_pnl') or 0) - bh:+.2f}")

    out_dir = ROOT / "output" / "orb" / "cta"
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = sym.replace("USDT", "")
    out_path = Path(args.json_out) if args.json_out else out_dir / f"gtl_honest_{tag}_{lo}_{hi}.json"
    payload = {
        "symbol": sym,
        "date_range": {"from": lo, "to": hi},
        "resample": rs,
        "aligned_setups": aligned_n,
        "sim": sim,
        "backtests": {
            k: {kk: (float(vv) if isinstance(vv, (int, float)) else vv) for kk, vv in v.items()}
            for k, v in results.items()
        },
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=lambda o: float(o) if hasattr(o, "item") else str(o)),
        encoding="utf-8",
    )
    print(f"\njson -> {out_path} ({time.time() - t0:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
