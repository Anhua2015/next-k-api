#!/usr/bin/env python3
"""批量单标 ORB 画像（训练池剩余标的）。

用法:
  python tools/orb/v2/batch_symbol_profiles.py
  python tools/orb/v2/batch_symbol_profiles.py --symbols-file config/orb_shared_train_symbols.txt
  python tools/orb/v2/batch_symbol_profiles.py --skip-existing
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi  # noqa: E402
from orb.core.kline_cache import has_kline_cache, norm_symbol  # noqa: E402
from orb.ml.samples import parse_symbol_list  # noqa: E402
from tools.orb.v2.explore_symbol_profile import LO_DEFAULT, HI_DEFAULT, explore, write_symbol_config  # noqa: E402

DEFAULT_POOL = ROOT / "config" / "orb" / "v2" / "symbols.txt"
DEFAULT_TRAIN = ROOT / "config" / "orb_shared_train_symbols.txt"
OUT_DIR = ROOT / "output" / "orb" / "v2" / "eval"


def _load_symbols(path: Path, *, exclude: List[str]) -> List[str]:
    raw = parse_symbol_list(path.read_text(encoding="utf-8"))
    ex = {s.replace("USDT", "").upper() for s in exclude}
    out: List[str] = []
    for s in raw:
        tag = norm_symbol(s).replace("USDT", "")
        if tag.upper() in ex:
            continue
        out.append(tag)
    return out


def _summary_row(report: Dict[str, Any]) -> Dict[str, Any]:
    rec = report.get("recommended") or {}
    base = report.get("or_baselines_1pct") or []
    best_base = max(base, key=lambda x: x.get("wallet_net_usdt", -1e9)) if base else {}
    ts = report.get("trade_summary_baseline") or {}
    sess = report.get("session_stats_by_or") or {}
    primary = str(report.get("primary_or_minutes") or rec.get("or_minutes") or "")
    ss = sess.get(primary) or {}
    return {
        "tag": report.get("tag"),
        "symbol": report.get("symbol"),
        "sessions_atr": (report.get("date_range") or {}).get("sessions_atr"),
        "avg_range_pct": ss.get("avg_range_pct"),
        "primary_or": report.get("primary_or_minutes"),
        "baseline_1pct_net": best_base.get("wallet_net_usdt"),
        "baseline_1pct_or": best_base.get("or_minutes"),
        "rec_or": rec.get("or_minutes"),
        "rec_risk_pct": rec.get("risk_pct"),
        "rec_tw": rec.get("trade_window_minutes"),
        "rec_net": rec.get("wallet_net_usdt"),
        "rec_wr": rec.get("win_rate"),
        "rec_opens": rec.get("opens"),
        "big_wins_5r": ts.get("big_wins_5r"),
        "win_rate_1pct": ts.get("win_rate_pct"),
        "target_4000": report.get("target_4000_usdt"),
        "personality": " | ".join(report.get("personality") or []),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Batch ORB symbol profiles")
    ap.add_argument("--symbols-file", default=str(DEFAULT_TRAIN))
    ap.add_argument("--exclude-file", default=str(DEFAULT_POOL), help="skip current live pool")
    ap.add_argument("--from-date", default=LO_DEFAULT)
    ap.add_argument("--to-date", default=HI_DEFAULT)
    ap.add_argument("--skip-existing", action="store_true", help="skip if *_profile.json exists")
    ap.add_argument("--no-write-config", action="store_true")
    ap.add_argument("--write-config-min-net", type=float, default=0.0, help="only write config/orb if rec net >= this")
    args = ap.parse_args()

    load_env_oi()
    exclude = parse_symbol_list(Path(args.exclude_file).read_text(encoding="utf-8")) if Path(args.exclude_file).is_file() else []
    symbols = _load_symbols(Path(args.symbols_file), exclude=exclude)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    log_path = OUT_DIR / "_batch_profiles_progress.txt"
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n=== batch start {time.strftime('%Y-%m-%d %H:%M:%S')} | {len(symbols)} symbols ===\n")

    print(f"Batch profile | {len(symbols)} symbols (exclude pool {len(exclude)})", flush=True)
    t_all = time.time()

    for i, tag in enumerate(symbols, 1):
        sym = norm_symbol(tag)
        prof_path = OUT_DIR / f"{tag.lower()}_profile.json"
        if args.skip_existing and prof_path.is_file():
            print(f"[{i}/{len(symbols)}] {tag} skip (exists)", flush=True)
            try:
                report = json.loads(prof_path.read_text(encoding="utf-8"))
                results.append(_summary_row(report))
            except Exception:
                pass
            continue
        if not has_kline_cache(sym, "5m"):
            print(f"[{i}/{len(symbols)}] {tag} skip (no 5m kline)", flush=True)
            errors.append({"tag": tag, "error": "no_kline"})
            continue

        t0 = time.time()
        print(f"[{i}/{len(symbols)}] {tag} ...", flush=True)
        try:
            report = explore(tag, args.from_date, args.to_date)
            row = _summary_row(report)
            results.append(row)
            rec_net = float(row.get("rec_net") or 0)
            if not args.no_write_config and rec_net >= float(args.write_config_min_net):
                write_symbol_config(report)
            line = (
                f"[{i}/{len(symbols)}] {tag} OR{row['rec_or']} risk={float(row['rec_risk_pct'] or 0)*100:.1f}% "
                f"net={rec_net:+.0f}U sessions={row['sessions_atr']} ({time.time()-t0:.0f}s)"
            )
            print(line, flush=True)
            with log_path.open("a", encoding="utf-8") as log:
                log.write(line + "\n")
        except Exception as e:
            err = {"tag": tag, "error": str(e), "trace": traceback.format_exc()}
            errors.append(err)
            print(f"[{i}/{len(symbols)}] {tag} ERROR: {e}", flush=True)
            with log_path.open("a", encoding="utf-8") as log:
                log.write(f"ERROR {tag}: {e}\n")

    results.sort(key=lambda r: float(r.get("rec_net") or -1e9), reverse=True)
    summary = {
        "date_range": {"from": args.from_date, "to": args.to_date},
        "symbols_requested": len(symbols),
        "symbols_ok": len(results),
        "errors": errors,
        "elapsed_sec": round(time.time() - t_all, 1),
        "tier_a_4000": [r for r in results if float(r.get("rec_net") or 0) >= 4000],
        "tier_b_500": [r for r in results if 500 <= float(r.get("rec_net") or 0) < 4000],
        "tier_c_positive": [r for r in results if 0 < float(r.get("rec_net") or 0) < 500],
        "tier_d_nonpos": [r for r in results if float(r.get("rec_net") or 0) <= 0],
        "results": results,
    }
    json_path = OUT_DIR / "universe_profile_summary.json"
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    csv_path = OUT_DIR / "universe_profile_summary.csv"
    if results:
        fields = list(results[0].keys())
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(results)

    print(f"\nDone {len(results)}/{len(symbols)} in {time.time()-t_all:.0f}s", flush=True)
    print(f"  tier A (>=4000U): {len(summary['tier_a_4000'])}", flush=True)
    print(f"  tier B (500-4000): {len(summary['tier_b_500'])}", flush=True)
    print(f"  tier C (0-500): {len(summary['tier_c_positive'])}", flush=True)
    print(f"  tier D (<=0): {len(summary['tier_d_nonpos'])}", flush=True)
    print(f"  -> {json_path}", flush=True)
    print(f"  -> {csv_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
