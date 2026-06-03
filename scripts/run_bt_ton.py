"""One-off: TONUSDT balanced + optimized replay backtest."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi

load_env_oi()

from moss_quant import config as cfg
from moss_quant.backtest_service import run_full_backtest
from moss_quant.kline_cache import load_cached
from moss_quant.optimize_service import run_strategy_optimize
from moss_quant.params import build_initial_params, resolve_params_dict

SYM = "TONUSDT"


def _best_params(best: dict) -> dict:
    tpl = str(best.get("template") or "balanced")
    params = build_initial_params(template=tpl)
    params.update(best.get("tactical_params") or {})
    if best.get("params"):
        for k, v in (best.get("params") or {}).items():
            if k in params or k in ("trailing_enabled",):
                params[k] = v
    return resolve_params_dict(params)


def _print_bt(label: str, s: dict, df) -> None:
    ret = float(s.get("total_return") or 0) * 100
    mdd = abs(float(s.get("max_drawdown") or 0)) * 100
    print(
        f"  {label}: return={ret:+.2f}%  sharpe={float(s.get('sharpe') or 0):.3f}  "
        f"mdd={mdd:.2f}%  trades={int(s.get('total_trades') or 0)}  "
        f"win={float(s.get('win_rate') or 0) * 100:.1f}%  blowup={int(s.get('blowup_count') or 0)}"
    )
    if df is not None and len(df):
        t0 = df["timestamp"].iloc[0]
        t1 = df["timestamp"].iloc[-1]
        print(f"  bars={len(df)}  kline {t0} -> {t1}")


def main() -> None:
    capital = float(cfg.MOSS_QUANT_DEFAULT_CAPITAL)
    print(
        f">>> {SYM} | source={cfg.MOSS_QUANT_DATA_SOURCE} "
        f"research_bars={cfg.MOSS_QUANT_RESEARCH_KLINE_BARS} capital={capital}"
    )
    print("=" * 72)

    print("\n[1] balanced 全窗 replay")
    params = build_initial_params(template="balanced")
    df = load_cached(SYM, refresh=True, research=True)
    out = run_full_backtest(
        symbol=SYM, params=params, capital=capital, refresh_klines=False
    )
    _print_bt("balanced", out.get("summary") or {}, df)

    print("\n[2] 网格寻优 + best 全窗 replay")
    opt = run_strategy_optimize(
        symbol=SYM, capital=capital, refresh_klines=False, top_n=3
    )
    best = opt.get("best")
    if not best or not best.get("summary"):
        print(f"  寻优无有效结果: {opt.get('warning') or 'no_valid_result'}")
        print(f"  tested={opt.get('combinations_tested')} ok={opt.get('combinations_ok')}")
        return
    sm = best.get("summary") or {}
    tact = best.get("tactical_params") or {}
    print(
        f"  best: template={best.get('template')} "
        f"entry={tact.get('entry_threshold')} sl={tact.get('sl_atr_mult')} "
        f"tp={tact.get('tp_rr_ratio')}"
    )
    print(
        f"  训练 return={float(sm.get('train_return', sm.get('total_return')) or 0) * 100:+.2f}% "
        f"val={float(sm.get('val_return') or 0) * 100:+.2f}% "
        f"WF={sm.get('wf_passed_folds')}/{sm.get('wf_folds')} "
        f"pool={sm.get('pool_tier')} sync={sm.get('sync_allowed')}"
    )
    if sm.get("wf_reason"):
        print(f"  {sm.get('wf_reason')}")
    if sm.get("sync_block_reason"):
        print(f"  sync_block: {sm.get('sync_block_reason')}")

    bt = run_full_backtest(
        symbol=SYM,
        params=_best_params(best),
        capital=capital,
        refresh_klines=False,
    )
    _print_bt("optimized full-window", bt.get("summary") or {}, df)
    print("=" * 72)


if __name__ == "__main__":
    main()
