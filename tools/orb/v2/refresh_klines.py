#!/usr/bin/env python3
"""定时刷新 universe K 线缓存 → data/orb/kline/。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi  # noqa: E402
from orb.core.kline_cache import norm_symbol  # noqa: E402
from orb.data.kline_fetch import KlineFetchError, cached_symbol_dirs, fetch_universe_klines  # noqa: E402
from orb.ml.model.auto_config import MlAutoConfig  # noqa: E402
from orb.ml.model.paths import resolve_train_symbols_path  # noqa: E402


def _parse_symbols_arg(raw: str) -> list[str]:
    out: list[str] = []
    for part in (raw or "").split(","):
        s = part.strip()
        if s:
            out.append(norm_symbol(s))
    return out


def main() -> int:
    load_env_oi()
    auto_cfg = MlAutoConfig.from_env()
    ap = argparse.ArgumentParser(description="Refresh ORB universe kline cache")
    ap.add_argument("--symbols-file", default="", help="默认 config/orb/v2/symbols.txt")
    ap.add_argument(
        "--symbol",
        default="",
        help="单标的（如 TSLA）；与 --symbols 二选一",
    )
    ap.add_argument(
        "--symbols",
        default="",
        help="逗号分隔多标的；留空则读 symbols-file 全部",
    )
    ap.add_argument("--from-date", default="", help="起始 session 日 YYYY-MM-DD（如 2026-02-01）")
    ap.add_argument("--to-date", default="", help="结束 session 日 YYYY-MM-DD（默认今天）")
    ap.add_argument("--days", type=float, default=0.0, help="未指定 --from-date 时用回看天数；0=ORB_ML_KLINE_DAYS")
    ap.add_argument(
        "--all-dirs",
        action="store_true",
        help="刷新 data/orb/kline 下全部已有目录（默认仅 symbols-file）",
    )
    ap.add_argument("--skip-existing", action="store_true", help="跳过已有完整缓存")
    ap.add_argument("--force-refresh", action="store_true", help="全量重拉（仍会与旧缓存 merge）")
    ap.add_argument("--no-merge", action="store_true", help="不合并旧缓存，仅保存本次拉取区间")
    ap.add_argument("--no-preflight", action="store_true", help="跳过 fapi 连通性探测")
    args = ap.parse_args()

    sym_file = Path(args.symbols_file) if args.symbols_file.strip() else resolve_train_symbols_path()
    days = float(args.days) if args.days > 0 else float(auto_cfg.kline_days)
    if args.force_refresh:
        skip_existing = False
    elif args.skip_existing:
        skip_existing = True
    else:
        skip_existing = auto_cfg.kline_skip_existing

    sym_override: list[str] | None = None
    if args.symbol.strip():
        sym_override = [norm_symbol(args.symbol.strip())]
    elif args.symbols.strip():
        sym_override = _parse_symbols_arg(args.symbols)
    elif args.all_dirs:
        sym_override = cached_symbol_dirs()
        print(f"[all-dirs] {len(sym_override)} symbols under data/orb/kline", flush=True)

    try:
        summary = fetch_universe_klines(
            symbols_file=sym_file,
            days=days,
            from_date=args.from_date.strip(),
            to_date=args.to_date.strip(),
            skip_existing=skip_existing,
            merge_existing=not bool(args.no_merge),
            symbols=sym_override,
            preflight=not bool(args.no_preflight),
        )
    except KlineFetchError as exc:
        print(f"ABORT: {exc}", flush=True)
        return 1

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if summary.get("errors") and auto_cfg.fail_on_kline_errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
