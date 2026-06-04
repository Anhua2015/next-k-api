#!/usr/bin/env python3
"""
本地扫描 Moss2 回测闸门：多窗长度 + 可选「更早一段」区间。

用法（在 next-k-api 目录）:
  python scripts/moss2_gate_window_sweep.py --symbols LINK,ICP,LTC
  python scripts/moss2_gate_window_sweep.py --seed-sample 8 --fetch
  python scripts/moss2_gate_window_sweep.py --symbols BTC --windows 800,1500,3000,4500 --offsets 0,1500

依赖: data/moss2_en_data_cache 下有 binanceusdm_* CSV，或 skills 里 BTC 148d 样例。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# next-k-api 根目录
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from moss2 import config as cfg
from moss2.dataset import normalize_symbol, resolve_csv_path
from moss2.params import build_initial_params, list_templates
from moss2.selection import (
    compete_templates,
    composite_score,
    passes_backtest_gates,
)
from moss2.discipline.report import build_discipline_report

# 线程上下文：给 load_ohlcv 打补丁，支持「跳过最近 N 根再取窗」
_SLICE_CTX: Dict[str, int] = {"tail_skip": 0}


def _install_load_ohlcv_slice_patch() -> None:
    import moss2.dataset as ds

    if getattr(ds.load_ohlcv, "_moss2_sweep_patched", False):
        return
    _orig = ds.load_ohlcv

    def _load(symbol: str, variant, *, limit: Optional[int] = None):
        path = ds.resolve_csv_path(symbol, variant)
        if not path or not path.is_file():
            raise FileNotFoundError(f"no CSV for {symbol} ({variant})")
        df = pd.read_csv(path, parse_dates=["timestamp"])
        skip = int(_SLICE_CTX.get("tail_skip") or 0)
        if skip > 0 and len(df) > skip:
            df = df.iloc[:-skip]
        if limit and len(df) > limit:
            df = df.iloc[-int(limit) :].copy()
        return df.reset_index(drop=True)

    _load._moss2_sweep_patched = True  # type: ignore[attr-defined]
    ds.load_ohlcv = _load  # type: ignore[assignment]


def gate_fail_reasons(
    summary: Dict[str, Any],
    discipline: Dict[str, Any],
    *,
    min_trades: int,
) -> List[str]:
    reasons: List[str] = []
    trades = int(summary.get("total_trades") or 0)
    if trades < min_trades:
        reasons.append(f"trades<{min_trades}({trades})")
    ev = float((discipline.get("ev") or {}).get("ev_per_trade_pct") or -1.0)
    if ev < float(cfg.MOSS2_SELECTION_MIN_EV_PCT):
        reasons.append(f"ev<{cfg.MOSS2_SELECTION_MIN_EV_PCT}({ev:.4f})")
    sharpe = float(summary.get("sharpe") or 0)
    if sharpe < float(cfg.MOSS2_SELECTION_MIN_SHARPE):
        reasons.append(f"sharpe<{cfg.MOSS2_SELECTION_MIN_SHARPE}({sharpe:.4f})")
    mdd = abs(float(summary.get("max_drawdown") or 0))
    if mdd > float(cfg.MOSS2_SELECTION_MAX_MDD):
        reasons.append(f"mdd>{cfg.MOSS2_SELECTION_MAX_MDD:.0%}({mdd:.4f})")
    return reasons


def best_coarse_row(
    symbol: str,
    *,
    limit_bars: int,
    min_trades: int,
    narrow: bool,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    comp = compete_templates(
        symbol,
        variant=cfg.MOSS2_OPS_VARIANT,
        limit_bars=limit_bars,
        optimize_tactical=narrow,
        min_trades=min_trades,
    )
    rows = comp.get("rows") or []
    best = comp.get("best")
    return best, rows


def sweep_one(
    symbol: str,
    *,
    windows: List[int],
    offsets: List[int],
    min_trades: int,
    narrow: bool,
) -> List[Dict[str, Any]]:
    sym = normalize_symbol(symbol, variant=cfg.MOSS2_OPS_VARIANT)
    csv_path = resolve_csv_path(sym, cfg.MOSS2_OPS_VARIANT)
    if not csv_path or not csv_path.is_file():
        return [
            {
                "symbol": sym,
                "csv": None,
                "error": "no_csv",
            }
        ]

    total_bars = len(pd.read_csv(csv_path, usecols=["timestamp"]))
    out: List[Dict[str, Any]] = []

    for skip in offsets:
        _SLICE_CTX["tail_skip"] = int(skip)
        for win in windows:
            if skip + win > total_bars:
                out.append(
                    {
                        "symbol": sym,
                        "window": win,
                        "tail_skip": skip,
                        "error": f"insufficient_bars(total={total_bars})",
                    }
                )
                continue
            try:
                best, coarse_rows = best_coarse_row(
                    sym, limit_bars=win, min_trades=min_trades, narrow=narrow
                )
            except Exception as e:
                out.append(
                    {
                        "symbol": sym,
                        "window": win,
                        "tail_skip": skip,
                        "error": str(e),
                    }
                )
                continue

            # 各模板最优失败原因（粗赛）
            per_tpl: Dict[str, str] = {}
            for tpl in list_templates():
                try:
                    params = build_initial_params(tpl, variant=cfg.MOSS2_OPS_VARIANT)  # type: ignore[arg-type]
                    from moss2.backtest_service import run_factory_backtest

                    bt = run_factory_backtest(
                        symbol=sym,
                        params=params,
                        variant=cfg.MOSS2_OPS_VARIANT,
                        limit_bars=win,
                    )
                    summ = bt.get("summary") or {}
                    disc = build_discipline_report(
                        summary=summ,
                        trades=bt.get("trades") or [],
                        template=tpl,
                    )
                    if passes_backtest_gates(summ, disc, min_trades=min_trades):
                        per_tpl[tpl] = "pass"
                    else:
                        per_tpl[tpl] = ",".join(
                            gate_fail_reasons(summ, disc, min_trades=min_trades)
                        ) or "fail"
                except Exception as ex:
                    per_tpl[tpl] = f"err:{ex}"

            row: Dict[str, Any] = {
                "symbol": sym,
                "csv": csv_path.name,
                "total_bars": total_bars,
                "window": win,
                "tail_skip": skip,
                "segment": _segment_label(skip, win, total_bars),
                "has_winner": bool(best),
                "min_trades": min_trades,
                "coarse_pass_count": len(coarse_rows),
            }
            if best:
                summ = best.get("summary") or {}
                disc = best.get("discipline") or {}
                row.update(
                    {
                        "best_template": best.get("template"),
                        "score": best.get("score"),
                        "trades": best.get("total_trades"),
                        "ev_pct": best.get("ev_per_trade_pct"),
                        "sharpe": best.get("sharpe"),
                        "mdd": best.get("max_drawdown"),
                        "return": summ.get("total_return"),
                    }
                )
            else:
                row["best_template"] = None
                row["per_template_fail"] = per_tpl
            out.append(row)
    return out


def _segment_label(tail_skip: int, window: int, total: int) -> str:
    if tail_skip == 0:
        return f"recent_{window}"
    end_idx = total - tail_skip
    start_idx = max(0, end_idx - window)
    return f"bars[{start_idx}:{end_idx}] (skip_recent={tail_skip})"


def _print_table(rows: List[Dict[str, Any]]) -> None:
    print()
    print(
        f"{'symbol':<12} {'segment':<28} {'win':>5} {'skip':>5} "
        f"{'winner':<12} {'pass':>4} {'trades':>6} {'ev%':>8} {'sharpe':>7} {'mdd':>7}"
    )
    print("-" * 105)
    winners = 0
    for r in rows:
        if r.get("error"):
            print(
                f"{r.get('symbol','?'):<12} {r.get('segment','?'):<28} "
                f"{r.get('window','?'):>5} {r.get('tail_skip',0):>5} "
                f"{'—':<12} {'—':>4} ERROR: {r['error']}"
            )
            continue
        w = r.get("has_winner")
        if w:
            winners += 1
        print(
            f"{r['symbol']:<12} {r.get('segment',''):<28} "
            f"{r['window']:>5} {r['tail_skip']:>5} "
            f"{str(r.get('best_template') or '—'):<12} "
            f"{r.get('coarse_pass_count',0):>4} "
            f"{r.get('trades') or 0:>6} "
            f"{r.get('ev_pct') or 0:>8.4f} "
            f"{r.get('sharpe') or 0:>7.3f} "
            f"{r.get('mdd') or 0:>7.3f}"
        )
        if not w and r.get("per_template_fail"):
            parts = [f"{k}:{v}" for k, v in r["per_template_fail"].items()]
            print(f"             fail → {' | '.join(parts)}")
    print("-" * 105)
    print(f"configs={len(rows)}  with_winner={winners}  no_winner={len(rows) - winners}")


def _maybe_fetch(bases: List[str]) -> None:
    from moss2.data_bootstrap import bootstrap_seed_data

    print(f"[fetch] pulling {bases} into {cfg.en_data_cache_dir()} ...")
    stats = bootstrap_seed_data(bases=bases, force=False, context="gate_sweep")
    print(
        f"[fetch] ok={stats.get('ok')} saved={stats.get('saved')} "
        f"skipped={stats.get('skipped')} failed={stats.get('failed')}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Moss2 gate sweep across windows/segments")
    parser.add_argument(
        "--symbols",
        default="LINK,ICP,LTC,NEAR,OP,SUI,TON,TRX,UNI,BTC",
        help="comma-separated bases or symbols",
    )
    parser.add_argument(
        "--seed-sample",
        type=int,
        default=0,
        help="if >0, take first N from MOSS2_SEED_BASES instead of --symbols",
    )
    parser.add_argument(
        "--windows",
        default="800,1500,3000,4500",
        help="comma-separated bar counts (15m bars)",
    )
    parser.add_argument(
        "--offsets",
        default="0,1500,3000",
        help="tail_skip: skip recent N bars before taking window",
    )
    parser.add_argument("--min-trades", type=int, default=5)
    parser.add_argument(
        "--narrow",
        action="store_true",
        help="run tactical narrow search (slower, same as production)",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="fetch missing CSV via bootstrap before sweep",
    )
    parser.add_argument(
        "--prefer-skills-btc",
        action="store_true",
        help="set MOSS2_PREFER_SKILLS_DATA_CACHE=1 (BTC 148d sample only)",
    )
    args = parser.parse_args()

    if args.prefer_skills_btc:
        os.environ["MOSS2_PREFER_SKILLS_DATA_CACHE"] = "1"

    _install_load_ohlcv_slice_patch()

    if args.seed_sample > 0:
        symbols = list(cfg.MOSS2_SEED_BASES[: args.seed_sample])
    else:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    windows = [int(x) for x in args.windows.split(",") if x.strip()]
    offsets = [int(x) for x in args.offsets.split(",") if x.strip()]

    print("Moss2 gate sweep")
    print(f"  cache_dir: {cfg.en_data_cache_dir()}")
    print(f"  gates: trades>={args.min_trades} (default sel={cfg.MOSS2_SELECTION_MIN_TRADES})")
    print(f"         ev>={cfg.MOSS2_SELECTION_MIN_EV_PCT} sharpe>={cfg.MOSS2_SELECTION_MIN_SHARPE}")
    print(f"         mdd<={cfg.MOSS2_SELECTION_MAX_MDD:.0%}")
    print(f"  symbols: {symbols}")
    print(f"  windows: {windows}  offsets(tail_skip): {offsets}  narrow={args.narrow}")

    if args.fetch:
        _maybe_fetch(symbols)

    all_rows: List[Dict[str, Any]] = []
    for sym in symbols:
        all_rows.extend(
            sweep_one(
                sym,
                windows=windows,
                offsets=offsets,
                min_trades=args.min_trades,
                narrow=args.narrow,
            )
        )

    _print_table(all_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
