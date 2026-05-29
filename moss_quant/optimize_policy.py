"""寻优评分、样本外验证、币池分层（每日寻优 / 市值扫描共用）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from moss_quant import config as cfg
from moss_quant.daily_auto_enable import evaluate_profile_auto_enable


def hard_reject_reason(summary: Optional[Dict[str, Any]]) -> Optional[str]:
    """训练窗硬淘汰（用于网格候选）。"""
    if not summary or summary.get("error"):
        return "invalid_summary"
    if int(summary.get("blowup_count") or 0) > 0:
        return "回测爆仓"
    trades = int(summary.get("total_trades") or 0)
    if trades < int(cfg.MOSS_QUANT_OPTIMIZE_MIN_TRAIN_TRADES):
        return "训练回合不足"
    if float(summary.get("total_return") or 0) <= 0:
        return "训练收益≤0"
    mdd = abs(float(summary.get("max_drawdown") or 0))
    if mdd > float(cfg.MOSS_QUANT_OPTIMIZE_MAX_TRAIN_DRAWDOWN):
        return "训练回撤过大"
    return None


def composite_optimize_score(summary: Dict[str, Any]) -> float:
    """复合评分：收益 + Sharpe + 回撤 + 笔数；硬淘汰返回 -999。"""
    reason = hard_reject_reason(summary)
    if reason:
        return -999.0
    ret = float(summary.get("total_return") or 0)
    sharpe = max(0.0, min(float(summary.get("sharpe") or 0), 2.0)) / 2.0
    mdd = abs(float(summary.get("max_drawdown") or 0))
    trades = int(summary.get("total_trades") or 0)
    trade_factor = min(trades / 12.0, 1.0)
    return round(
        0.45 * ret + 0.25 * sharpe + 0.20 * (1.0 - mdd) + 0.10 * trade_factor,
        6,
    )


def split_train_validation_df(
    df: pd.DataFrame, train_ratio: Optional[float] = None
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    ratio = float(train_ratio if train_ratio is not None else cfg.MOSS_QUANT_OPTIMIZE_TRAIN_RATIO)
    ratio = max(0.5, min(0.85, ratio))
    n = int(len(df))
    min_bars = int(cfg.MOSS_QUANT_OPTIMIZE_MIN_BARS)
    if n < min_bars:
        return df.copy(), df.iloc[0:0].copy()
    cut = max(int(n * ratio), int(n * 0.5))
    cut = min(cut, n - max(48, int(n * 0.15)))
    if cut <= 0 or cut >= n:
        return df.copy(), df.iloc[0:0].copy()
    return df.iloc[:cut].copy().reset_index(drop=True), df.iloc[cut:].copy().reset_index(
        drop=True
    )


def regime_tactical_adjustments(regime: pd.Series) -> Dict[str, Any]:
    """训练窗 regime 占比 → 战术微调（不替换模板）。"""
    if regime is None or len(regime) == 0:
        return {}
    vc = regime.astype(str).value_counts(normalize=True)
    sideways = float(
        vc.get("SIDEWAYS", 0)
        + vc.get("CHOP", 0)
        + vc.get("RANGE", 0)
    )
    trend = float(
        vc.get("TREND_UP", 0)
        + vc.get("TREND_DOWN", 0)
        + vc.get("BULL", 0)
        + vc.get("BEAR", 0)
        + vc.get("UPTREND", 0)
        + vc.get("DOWNTREND", 0)
    )
    out: Dict[str, Any] = {}
    if sideways >= 0.55:
        out["entry_threshold_bump"] = 0.04
        out["tp_rr_mult"] = 0.9
        out["regime_note"] = "sideways_heavy"
    elif trend >= 0.45:
        out["entry_threshold_bump"] = -0.02
        out["regime_note"] = "trend_heavy"
    return out


def apply_regime_to_tactical(tactical: Dict[str, Any], regime_adj: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(tactical)
    bump = float(regime_adj.get("entry_threshold_bump") or 0)
    if bump and "entry_threshold" in out:
        out["entry_threshold"] = round(
            max(0.05, min(0.60, float(out["entry_threshold"]) + bump)), 4
        )
    mult = float(regime_adj.get("tp_rr_mult") or 1.0)
    if mult != 1.0 and "tp_rr_ratio" in out:
        out["tp_rr_ratio"] = round(max(1.0, min(10.0, float(out["tp_rr_ratio"]) * mult)), 4)
    return out


def validation_fail_reason(summary: Optional[Dict[str, Any]]) -> Optional[str]:
    if not summary or summary.get("error"):
        return "验证窗无结果"
    if float(summary.get("total_return") or 0) <= 0:
        return "验证收益≤0"
    mdd = abs(float(summary.get("max_drawdown") or 0))
    if mdd > float(cfg.MOSS_QUANT_OPTIMIZE_MAX_VAL_DRAWDOWN):
        return "验证回撤过大"
    trades = int(summary.get("total_trades") or 0)
    if trades < int(cfg.MOSS_QUANT_OPTIMIZE_MIN_VAL_TRADES):
        return "验证回合不足"
    return None


def evaluate_validation(summary: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    reason = validation_fail_reason(summary)
    return {
        "validation_passed": reason is None,
        "validation_reason": reason or "验证通过",
    }


def classify_pool_tier(summary: Dict[str, Any]) -> Dict[str, Any]:
    """A=可交易 B=观察 C=剔除（用于看板与同步）。"""
    if summary.get("error"):
        return {"pool_tier": "C", "pool_label": "剔除", "pool_reason": "寻优失败"}

    gate = evaluate_profile_auto_enable(summary)
    val_passed = bool(summary.get("validation_passed"))
    val_required = bool(cfg.MOSS_QUANT_OPTIMIZE_REQUIRE_VALIDATION)

    if not gate.get("auto_enabled"):
        return {
            "pool_tier": "C",
            "pool_label": "剔除",
            "pool_reason": str(gate.get("auto_enable_reason") or "不达标"),
        }
    if val_required and not val_passed:
        reason = str(summary.get("validation_reason") or "验证未通过")
        return {"pool_tier": "B", "pool_label": "观察", "pool_reason": reason}
    if summary.get("mcap_observation"):
        return {
            "pool_tier": "B",
            "pool_label": "观察",
            "pool_reason": "市值扩币观察期",
        }
    return {
        "pool_tier": "A",
        "pool_label": "可交易",
        "pool_reason": "训练+验证+门槛通过",
    }


def can_sync_profile_params(summary: Dict[str, Any]) -> bool:
    """是否允许将本批次寻优结果写入纸面 Profile。"""
    if summary.get("error"):
        return False
    tier = classify_pool_tier(summary)
    if tier["pool_tier"] != "A":
        return False
    if bool(cfg.MOSS_QUANT_OPTIMIZE_REQUIRE_VALIDATION) and not summary.get(
        "validation_passed"
    ):
        return False
    return bool(evaluate_profile_auto_enable(summary).get("auto_enabled"))


def enrich_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    """合并门禁、验证、币池标签到 summary（写入 DB / API）。"""
    out = dict(summary)
    out.update(evaluate_profile_auto_enable(out))
    if "validation_passed" not in out:
        val_summary = out.get("validation_summary")
        if isinstance(val_summary, dict):
            out.update(evaluate_validation(val_summary))
    out.update(classify_pool_tier(out))
    out["sync_allowed"] = can_sync_profile_params(out)
    return out


def pick_best_validated(
    candidates: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """在通过验证的候选中选验证 Sharpe 最高者；关闭验证门控时取训练分最高。"""
    ok = [
        c
        for c in candidates
        if c.get("summary")
        and float(c.get("score") or -999) > -900
        and (
            c.get("validation", {}).get("validation_passed")
            or not cfg.MOSS_QUANT_OPTIMIZE_REQUIRE_VALIDATION
        )
    ]
    if not ok:
        return None
    if not cfg.MOSS_QUANT_OPTIMIZE_REQUIRE_VALIDATION:
        ok.sort(
            key=lambda r: (
                -float(r.get("score") or -999),
                -float((r.get("summary") or {}).get("sharpe") or -999),
            )
        )
        return ok[0]
    ok.sort(
        key=lambda r: (
            -float((r.get("validation") or {}).get("val_sharpe") or -999),
            -float((r.get("validation") or {}).get("val_return") or -999),
            -float(r.get("score") or -999),
        )
    )
    return ok[0]


def risk_scale_for_rank(rank_index: int) -> float:
    """A 池内排序：前 N 满仓，其余半仓（1x 下 risk 缩放）。"""
    full = max(1, int(cfg.MOSS_QUANT_OPTIMIZE_FULL_RISK_SLOTS))
    if rank_index < full:
        return 1.0
    return float(cfg.MOSS_QUANT_OPTIMIZE_REDUCED_RISK_SCALE)
