"""Walk-forward OOS validation for pairs (Ruuj: re-screen + trade on unseen slice)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from pairs.backtest import PairsBacktestConfig, run_pairs_backtest
from pairs.cointegration import cointegration_stats


def walk_forward_slices(
    n_bars: int,
    *,
    train_bars: int,
    test_bars: int,
    step_bars: Optional[int] = None,
) -> List[Tuple[int, int, int]]:
    """Return (train_start, train_end, test_end) bar indices for each fold."""
    step = step_bars or test_bars
    folds: List[Tuple[int, int, int]] = []
    start = 0
    while start + train_bars + test_bars <= n_bars:
        folds.append((start, start + train_bars, start + train_bars + test_bars))
        start += step
    return folds


def run_walk_forward(
    prices: pd.DataFrame,
    cfg: PairsBacktestConfig,
    *,
    train_bars: int = 90 * 24,
    test_bars: int = 30 * 24,
    step_bars: Optional[int] = None,
    adf_max_p: float = 0.10,
    require_coint: bool = True,
) -> Dict[str, Any]:
    """Run OOS folds: cointegration check on train, trade test slice with fixed cfg params."""
    n = len(prices)
    folds_meta = walk_forward_slices(n, train_bars=train_bars, test_bars=test_bars, step_bars=step_bars)
    if not folds_meta:
        return {"error": "no_folds", "bars": n, "train_bars": train_bars, "test_bars": test_bars}

    fold_rows: List[dict] = []
    oos_pnls: List[float] = []
    oos_trips = 0

    for i, (t0, t1, t2) in enumerate(folds_meta):
        train = prices.iloc[t0:t1].reset_index(drop=True)
        test = prices.iloc[t1:t2].reset_index(drop=True)
        cg = cointegration_stats(train.leg1, train.leg2)
        ok_coint = cg["adf_pvalue"] < adf_max_p or (cg.get("half_life_bars") or 999) < 72

        row: dict = {
            "fold": i + 1,
            "train_bars": len(train),
            "test_bars": len(test),
            "train_start_ms": int(train.open_time.iloc[0]),
            "test_end_ms": int(test.open_time.iloc[-1]),
            "adf_pvalue": cg["adf_pvalue"],
            "half_life_bars": cg.get("half_life_bars"),
            "cointegrated": ok_coint,
        }

        if require_coint and not ok_coint:
            row.update({"skipped": True, "reason": "cointegration_fail"})
            fold_rows.append(row)
            continue

        test_cfg = replace(cfg, initial_capital_usdt=cfg.initial_capital_usdt)
        res = run_pairs_backtest(test, test_cfg)
        w = res.get("wallet") or {}
        row.update(
            {
                "skipped": False,
                "round_trips": w.get("round_trips", 0),
                "pnl_usdt": w.get("total_pnl_usdt", 0.0),
                "return_pct": w.get("total_return_pct", 0.0),
                "max_drawdown_usdt": w.get("max_drawdown_usdt", 0.0),
            }
        )
        oos_pnls.append(float(w.get("total_pnl_usdt") or 0.0))
        oos_trips += int(w.get("round_trips") or 0)
        fold_rows.append(row)

    traded = [r for r in fold_rows if not r.get("skipped")]
    total_oos_pnl = sum(oos_pnls)
    cap = float(cfg.initial_capital_usdt)
    return {
        "leg1": cfg.leg1,
        "leg2": cfg.leg2,
        "bars": n,
        "folds": len(folds_meta),
        "folds_traded": len(traded),
        "train_bars": train_bars,
        "test_bars": test_bars,
        "step_bars": step_bars or test_bars,
        "oos_total_pnl_usdt": round(total_oos_pnl, 2),
        "oos_return_pct": round(total_oos_pnl / cap * 100, 2) if cap > 0 else 0.0,
        "oos_round_trips": oos_trips,
        "fold_details": fold_rows,
    }
