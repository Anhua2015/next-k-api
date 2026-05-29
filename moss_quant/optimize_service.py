"""网格搜索：模板 + 战术参数 + 训练/验证窗 + 复合评分。"""

from __future__ import annotations

import itertools
from typing import Any, Dict, List, Optional

import pandas as pd

from moss_quant import config as cfg
from moss_quant.core.backtest import run_backtest
from moss_quant.core.decision import DecisionParams
from moss_quant.core.regime import classify_regime
from moss_quant.kline_cache import load_cached
from moss_quant.optimize_policy import (
    apply_regime_to_tactical,
    composite_optimize_score,
    enrich_summary,
    hard_reject_reason,
    pick_best_validated,
    regime_tactical_adjustments,
    split_train_validation_df,
    validation_fail_reason,
)
from moss_quant.params import (
    TACTICAL_FLOAT_FIELDS,
    build_initial_params,
    cap_leverage_for_symbol,
    resolve_params_dict,
)

TEMPLATES = ("balanced", "momentum", "trend", "mean_revert")

DEFAULT_ENTRY_THRESHOLDS = (0.40, 0.44, 0.48, 0.52)
DEFAULT_SL_ATR_MULTS = (2.0, 2.5)
DEFAULT_TP_RR_RATIOS = (2.0, 2.5, 3.0)

TACTICAL_GRID_KEYS = (
    "entry_threshold",
    "sl_atr_mult",
    "tp_rr_ratio",
    "exit_threshold",
    "regime_sensitivity",
)

TRAILING_TEMPLATES = frozenset({"momentum", "trend"})


def _trailing_for_template(template: str) -> bool:
    return (
        cfg.MOSS_QUANT_OPTIMIZE_TRAILING_FOR_TREND
        and str(template).lower() in TRAILING_TEMPLATES
    )


def _build_run_params(
    template: str, tactical: Dict[str, Any], *, symbol: str
) -> Dict[str, Any]:
    params = build_initial_params(template=template)
    params.update(tactical)
    if _trailing_for_template(template):
        params["trailing_enabled"] = True
    return cap_leverage_for_symbol(resolve_params_dict(params), symbol)


def _optimize_score(summary: Dict[str, Any]) -> float:
    return composite_optimize_score(summary)


def _run_backtest_summary(
    df: pd.DataFrame,
    regime: pd.Series,
    *,
    symbol: str,
    template: str,
    tactical: Dict[str, Any],
    capital: float,
) -> Dict[str, Any]:
    params = _build_run_params(template, tactical, symbol=symbol)
    p = DecisionParams.from_dict(params)
    result = run_backtest(
        df,
        p,
        regime,
        initial_capital=capital,
        symbol=symbol,
    )
    return {
        "total_return": round(result.total_return, 4),
        "sharpe": round(result.sharpe_ratio, 4),
        "max_drawdown": round(result.max_drawdown, 4),
        "total_trades": int(result.total_trades),
        "win_rate": round(result.win_rate, 4),
        "blowup_count": int(result.blowup_count),
    }


def _run_one(
    df: pd.DataFrame,
    regime: pd.Series,
    *,
    symbol: str,
    template: str,
    tactical: Dict[str, Any],
    capital: float,
) -> Dict[str, Any]:
    summary = _run_backtest_summary(
        df,
        regime,
        symbol=symbol,
        template=template,
        tactical=tactical,
        capital=capital,
    )
    reject = hard_reject_reason(summary)
    params_full = _build_run_params(template, tactical, symbol=symbol)
    tact_out = {k: tactical[k] for k in TACTICAL_GRID_KEYS if k in tactical}
    for k in TACTICAL_FLOAT_FIELDS:
        if k in params_full and k not in tact_out:
            tact_out[k] = params_full[k]
    if params_full.get("trailing_enabled"):
        tact_out["trailing_enabled"] = True
    return {
        "template": template,
        "tactical_params": tact_out,
        "params": params_full,
        "summary": summary,
        "train_reject": reject,
        "score": _optimize_score(summary),
    }


def _validate_candidate(
    candidate: Dict[str, Any],
    df_val: pd.DataFrame,
    regime_val: pd.Series,
    *,
    symbol: str,
    capital: float,
) -> Dict[str, Any]:
    if len(df_val) < 48:
        return {
            "validation_passed": False,
            "validation_reason": "验证窗K线不足",
            "validation_summary": None,
            "val_sharpe": None,
            "val_return": None,
        }
    tactical = dict(candidate.get("tactical_params") or {})
    template = str(candidate.get("template") or "balanced")
    try:
        val_summary = _run_backtest_summary(
            df_val,
            regime_val,
            symbol=symbol,
            template=template,
            tactical=tactical,
            capital=capital,
        )
    except Exception as e:
        return {
            "validation_passed": False,
            "validation_reason": str(e),
            "validation_summary": None,
            "val_sharpe": None,
            "val_return": None,
        }
    reason = validation_fail_reason(val_summary)
    return {
        "validation_passed": reason is None,
        "validation_reason": reason or "验证通过",
        "validation_summary": val_summary,
        "val_sharpe": float(val_summary.get("sharpe") or 0),
        "val_return": float(val_summary.get("total_return") or 0),
    }


def _attach_best_metadata(
    best: Dict[str, Any],
    *,
    train_bars: int,
    val_bars: int,
    regime_adj: Dict[str, Any],
) -> Dict[str, Any]:
    train_summary = dict(best.get("summary") or {})
    val_block = dict(best.get("validation") or {})
    val_summary = val_block.get("validation_summary") or {}
    merged = {
        **train_summary,
        "train_score": float(best.get("score") or 0),
        "train_bars": train_bars,
        "val_bars": val_bars,
        "validation_passed": bool(val_block.get("validation_passed")),
        "validation_reason": val_block.get("validation_reason"),
        "validation_summary": val_summary,
        "val_sharpe": val_block.get("val_sharpe"),
        "val_return": val_block.get("val_return"),
        "regime_adjustment": regime_adj or {},
    }
    if val_summary:
        merged["val_total_trades"] = val_summary.get("total_trades")
        merged["val_max_drawdown"] = val_summary.get("max_drawdown")
    best = dict(best)
    best["summary"] = enrich_summary(merged)
    return best


def run_strategy_optimize(
    *,
    symbol: str,
    capital: Optional[float] = None,
    refresh_klines: bool = False,
    regime_version: Optional[str] = None,
    top_n: int = 15,
    max_combinations: int = 96,
    entry_thresholds: Optional[List[float]] = None,
    sl_atr_mults: Optional[List[float]] = None,
    tp_rr_ratios: Optional[List[float]] = None,
    mcap_observation: bool = False,
) -> Dict[str, Any]:
    """训练窗网格寻优 → Top-K → 验证窗样本外 → 复合评分入选。"""
    capital = float(capital or cfg.MOSS_QUANT_DEFAULT_CAPITAL)
    regime_version = regime_version or cfg.MOSS_QUANT_REGIME_VERSION
    sym = str(symbol).strip().upper()

    entries = tuple(entry_thresholds or DEFAULT_ENTRY_THRESHOLDS)
    sls = tuple(sl_atr_mults or DEFAULT_SL_ATR_MULTS)
    tps = tuple(tp_rr_ratios or DEFAULT_TP_RR_RATIOS)

    grid = list(itertools.product(TEMPLATES, entries, sls, tps))
    if len(grid) > max_combinations:
        grid = grid[:max_combinations]

    df_full = load_cached(sym, refresh=refresh_klines)
    df_train, df_val = split_train_validation_df(df_full)
    regime_train = classify_regime(df_train, version=regime_version)
    regime_val = (
        classify_regime(df_val, version=regime_version)
        if len(df_val) > 0
        else pd.Series(dtype=str)
    )
    regime_adj = regime_tactical_adjustments(regime_train)

    results: List[Dict[str, Any]] = []
    for template, entry, sl, tp in grid:
        tactical = apply_regime_to_tactical(
            {
                "entry_threshold": float(entry),
                "sl_atr_mult": float(sl),
                "tp_rr_ratio": float(tp),
                "exit_threshold": 0.12,
                "regime_sensitivity": 0.55,
            },
            regime_adj,
        )
        try:
            row = _run_one(
                df_train,
                regime_train,
                symbol=sym,
                template=template,
                tactical=tactical,
                capital=capital,
            )
            results.append(row)
        except Exception as e:
            results.append(
                {
                    "template": template,
                    "tactical_params": tactical,
                    "error": str(e),
                    "score": -999.0,
                    "summary": None,
                }
            )

    valid = [r for r in results if r.get("summary") and float(r.get("score") or -999) > -900]
    valid.sort(
        key=lambda r: (
            -float(r.get("score") or -999),
            -float((r.get("summary") or {}).get("sharpe") or -999),
        )
    )

    top_k = max(1, min(int(cfg.MOSS_QUANT_OPTIMIZE_VALIDATION_TOP_K), len(valid)))
    candidates = valid[:top_k]
    validated: List[Dict[str, Any]] = []
    for cand in candidates:
        c2 = dict(cand)
        c2["validation"] = _validate_candidate(
            cand, df_val, regime_val, symbol=sym, capital=capital
        )
        validated.append(c2)

    best_raw = pick_best_validated(validated)
    if best_raw is None and valid:
        best_raw = dict(valid[0])
        if "validation" not in best_raw:
            best_raw["validation"] = _validate_candidate(
                best_raw, df_val, regime_val, symbol=sym, capital=capital
            )

    best: Optional[Dict[str, Any]] = None
    if best_raw:
        best = _attach_best_metadata(
            best_raw,
            train_bars=int(len(df_train)),
            val_bars=int(len(df_val)),
            regime_adj=regime_adj,
        )
        if mcap_observation and best.get("summary"):
            best["summary"] = enrich_summary(
                {**best["summary"], "mcap_observation": True}
            )

    top_n = max(1, min(int(top_n), 50))
    ranking: List[Dict[str, Any]] = []
    for c in validated[:top_n]:
        if not c.get("summary"):
            continue
        ranking.append(
            _attach_best_metadata(
                c,
                train_bars=int(len(df_train)),
                val_bars=int(len(df_val)),
                regime_adj=regime_adj,
            )
        )

    best_ret = float((best or {}).get("summary", {}).get("total_return", 0) or 0)
    all_non_positive = bool(valid) and best_ret <= 0
    val_passed = bool((best or {}).get("summary", {}).get("validation_passed"))

    return {
        "ok": True,
        "symbol": sym,
        "capital": capital,
        "data_source": cfg.MOSS_QUANT_DATA_SOURCE,
        "data_source_label": cfg.data_source_label(),
        "combinations_tested": len(grid),
        "combinations_ok": len(valid),
        "bars": int(len(df_full)),
        "train_bars": int(len(df_train)),
        "val_bars": int(len(df_val)),
        "kline_start": str(df_full["timestamp"].iloc[0]) if len(df_full) else None,
        "kline_end": str(df_full["timestamp"].iloc[-1]) if len(df_full) else None,
        "best": best,
        "validation_passed": val_passed,
        "all_non_positive": all_non_positive,
        "warning": (
            "本次窗口内所有组合收益均≤0，最优仅为相对亏损最小；不宜直接应用实盘。"
            if all_non_positive
            else (
                "最优组合未通过样本外验证，不会自动同步纸面 Profile。"
                if best and not val_passed and cfg.MOSS_QUANT_OPTIMIZE_REQUIRE_VALIDATION
                else None
            )
        ),
        "ranking": ranking,
        "regime_adjustment": regime_adj,
        "search_space": {
            "templates": list(TEMPLATES),
            "entry_threshold": list(entries),
            "sl_atr_mult": list(sls),
            "tp_rr_ratio": list(tps),
            "fixed": {"exit_threshold": 0.12, "regime_sensitivity": 0.55},
            "train_ratio": float(cfg.MOSS_QUANT_OPTIMIZE_TRAIN_RATIO),
        },
    }
