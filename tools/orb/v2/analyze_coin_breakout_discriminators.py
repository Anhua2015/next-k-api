#!/usr/bin/env python3
"""COIN OR10：真突破 vs 假突破 — 前日收盘 & 开盘后/入场特征对比。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi  # noqa: E402
from orb.core.backtest import _daily_df_asof  # noqa: E402
from orb.core.config import OrbConfig  # noqa: E402
from orb.core.indicators import compute_atr_series, daily_atr_asof  # noqa: E402
from orb.core.kline_cache import load_klines  # noqa: E402
from orb.core.rth_daily import aggregate_rth_daily_bars  # noqa: E402
from orb.core.session import (  # noqa: E402
    compute_opening_range,
    session_anchor_ms,
    session_close_ms,
    session_slice,
)
from tools.orb.v2.explore_symbol_profile import _enrich_trades  # noqa: E402

TRADES_CSV = ROOT / "output" / "orb" / "v2" / "eval" / "coin_or10_3pct_tw0.trades.csv"
OUT_JSON = ROOT / "output" / "orb" / "v2" / "eval" / "coin_or10_breakout_discriminators.json"
RISK_PCT = 0.03
OR_MIN = 10


def _label(row: pd.Series) -> str:
    pnl_r = float(row.get("pnl_r") or 0)
    outcome = str(row.get("outcome") or "")
    if pnl_r >= 5:
        return "true_fat"  # 真突破 fat tail
    if outcome == "loss" or float(row.get("pnl_usdt") or 0) <= 0 and outcome != "session_close":
        return "fake_sl"  # 假突破：止损
    if float(row.get("pnl_usdt") or 0) > 0:
        return "true_small"  # 真但小赢
    return "fake_eod"  # 扛到收盘仍亏


def _prev_day_features(daily_rth: pd.DataFrame, daily_b1d: pd.DataFrame, day: str, cfg: OrbConfig) -> Dict[str, Any]:
    ts = pd.Timestamp(f"{day} 12:00:00", tz=cfg.session_tz)
    anchor = session_anchor_ms(int(ts.value // 1_000_000), tz=cfg.session_tz, session_open_time=cfg.session_open_time)
    ddf_rth = _daily_df_asof(daily_rth, anchor)
    atr_rth = daily_atr_asof(daily_rth, anchor, period=14, tz=cfg.session_tz)
    atr_b1d = daily_atr_asof(daily_b1d, anchor, period=14, tz=cfg.session_tz)
    prev_close = float(ddf_rth["close"].iloc[-1]) if not ddf_rth.empty else np.nan

    d = daily_rth.drop_duplicates("open_time").sort_values("open_time")
    d["atr"] = compute_atr_series(d, period=14)
    d["atr_pct"] = d["atr"] / d["close"] * 100
    d["range_pct"] = (d["high"] - d["low"]) / d["close"] * 100
    d["ret_pct"] = d["close"].pct_change() * 100
    hist = d[d["open_time"] < anchor]
    if hist.empty:
        return {}
    prev = hist.iloc[-1]
    h20 = hist.tail(20)
    atr_pct = float(atr_rth) / prev_close * 100 if prev_close and atr_rth else np.nan
    rank = float((h20["atr_pct"] < atr_pct).mean() * 100) if len(h20) >= 5 and not np.isnan(atr_pct) else np.nan
    return {
        "prev_rth_atr_pct": round(atr_pct, 3) if not np.isnan(atr_pct) else None,
        "prev_b1d_atr_pct": round(float(atr_b1d) / prev_close * 100, 3) if prev_close and atr_b1d else None,
        "prev_atr_rank20": round(rank, 1) if not np.isnan(rank) else None,
        "prev_day_range_pct": round(float(prev["range_pct"]), 3),
        "prev_day_ret_pct": round(float(prev["ret_pct"]), 3) if pd.notna(prev["ret_pct"]) else None,
        "prev_day_vol_vs20": round(float(prev["volume"]) / float(h20["volume"].mean()), 3) if len(h20) >= 5 else None,
    }


def _entry_features(df5: pd.DataFrame, t: pd.Series, cfg: OrbConfig) -> Dict[str, Any]:
    d = str(t["session_date"])
    side = str(t["side"]).upper()
    entry = float(t["entry"])
    fill_ms = int(t.get("fill_bar_open_ms") or t.get("scan_open_ms") or 0)
    ts = pd.Timestamp(f"{d} 12:00:00", tz=cfg.session_tz)
    anchor = session_anchor_ms(int(ts.value // 1_000_000), tz=cfg.session_tz, session_open_time=cfg.session_open_time)
    or_end = anchor + OR_MIN * 60_000
    sess = session_slice(df5, fill_ms, tz=cfg.session_tz, session_open_time=cfg.session_open_time)
    if sess.empty:
        return {}
    open_p = float(sess.iloc[0]["open"])
    pack = compute_opening_range(
        sess,
        or_minutes=OR_MIN,
        bar_step_ms=300_000,
        asof_open_ms=min(fill_ms, or_end + 300_000),
        tz=cfg.session_tz,
        session_open_time=cfg.session_open_time,
    )
    or_w = float(pack["or_width_pct"]) if pack else np.nan
    or_h = float(pack["or_high"]) if pack else np.nan
    or_l = float(pack["or_low"]) if pack else np.nan
    move_at_entry = (entry - open_p) / open_p * 100 if side == "LONG" else (open_p - entry) / open_p * 100
    aligned = move_at_entry > 0
    break_ext_pct = (
        (entry - or_h) / open_p * 100 if side == "LONG" and or_h else (or_l - entry) / open_p * 100 if or_l else np.nan
    )

    # 开盘后前 30min（至 fill 或 OR+30）波动
    early_end = min(fill_ms + 30 * 60_000, or_end + 30 * 60_000)
    early = df5[(df5["open_time"] >= anchor) & (df5["open_time"] <= early_end)]
    early_range = (early["high"].max() - early["low"].min()) / open_p * 100 if not early.empty else np.nan

    # fill 后 15/30 分钟方向
    def _ret_after(mins: int) -> float | None:
        end = fill_ms + mins * 60_000
        path = df5[(df5["open_time"] >= fill_ms) & (df5["open_time"] <= end)]
        if len(path) < 2:
            return None
        px = float(path.iloc[-1]["close"])
        r = (px - entry) / entry * 100
        return round(-r if side == "SHORT" else r, 3)

    return {
        "or_width_pct": round(or_w, 3) if not np.isnan(or_w) else None,
        "move_at_entry_pct": round(move_at_entry, 3),
        "trend_aligned_at_entry": bool(aligned),
        "break_extension_pct": round(break_ext_pct, 3) if not np.isnan(break_ext_pct) else None,
        "mins_after_or": round(max(0, (fill_ms - or_end) / 60_000), 1),
        "early_30m_range_pct": round(float(early_range), 3) if not np.isnan(early_range) else None,
        "ret_15m_after_fill": _ret_after(15),
        "ret_30m_after_fill": _ret_after(30),
    }


def _group_stats(df: pd.DataFrame, cols: List[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for g in ("true_fat", "fake_sl", "true_small", "fake_eod"):
        sub = df[df["label"] == g]
        if sub.empty:
            continue
        out[g] = {"n": int(len(sub)), **{c: round(float(sub[c].mean()), 3) for c in cols if c in sub.columns and sub[c].notna().any()}}
    return out


def main() -> int:
    load_env_oi()
    cfg = OrbConfig.from_env()
    cfg.or_minutes = OR_MIN
    cfg.risk_pct = RISK_PCT

    trades_raw = pd.read_csv(TRADES_CSV).to_dict(orient="records")
    df5 = load_klines("COINUSDT", "5m")
    df5w = load_klines("COINUSDT", "5m")  # for rth aggregate
    daily_rth = aggregate_rth_daily_bars(df5w, cfg)
    daily_b1d = load_klines("COINUSDT", "1d")

    enriched = _enrich_trades(trades_raw, df5, cfg, OR_MIN)
    rows: List[Dict[str, Any]] = []
    tmap = {str(t["session_date"]): t for t in trades_raw}
    for _, r in enriched.iterrows():
        d = str(r["session_date"])
        t = tmap[d]
        row = {**r.to_dict(), **_prev_day_features(daily_rth, daily_b1d, d, cfg), **_entry_features(df5, pd.Series(t), cfg)}
        row["label"] = _label(pd.Series(row))
        rows.append(row)

    df = pd.DataFrame(rows)
    compare_cols = [
        "prev_rth_atr_pct",
        "prev_b1d_atr_pct",
        "prev_atr_rank20",
        "prev_day_range_pct",
        "prev_day_ret_pct",
        "or_width_pct",
        "move_at_entry_pct",
        "break_extension_pct",
        "early_30m_range_pct",
        "h1_ret_pct",
        "mfe_pct",
        "mae_pct",
        "ret_15m_after_fill",
        "ret_30m_after_fill",
    ]
    stats = _group_stats(df, compare_cols)

    fat = df[df["label"] == "true_fat"]
    fake = df[df["label"] == "fake_sl"]

    # 简单规则扫描：前日 + 开盘后
    rules: List[Dict[str, Any]] = []

    def _eval(name: str, mask: pd.Series) -> None:
        sub = df[mask]
        if len(sub) < 5:
            return
        fat_c = int((sub["label"] == "true_fat").sum())
        fake_c = int((sub["label"] == "fake_sl").sum())
        rules.append(
            {
                "rule": name,
                "n": int(len(sub)),
                "true_fat": fat_c,
                "fake_sl": fake_c,
                "fat_rate": round(fat_c / len(sub) * 100, 1),
                "fake_rate": round(fake_c / len(sub) * 100, 1),
                "net_pnl": round(float(sub["pnl_usdt"].sum()), 1),
            }
        )

    med_atr = float(df["prev_rth_atr_pct"].median())
    med_or = float(df["or_width_pct"].median())
    _eval("baseline", pd.Series(True, index=df.index))
    _eval(f"prev_atr_rank20<=50", df["prev_atr_rank20"] <= 50)
    _eval(f"prev_rth_atr<{med_atr:.2f}%", df["prev_rth_atr_pct"] < med_atr)
    _eval(f"or_width>={med_or:.2f}%", df["or_width_pct"] >= med_or)
    _eval("trend_aligned@entry", df["trend_aligned_at_entry"])
    _eval("ret_15m_after_fill>0", df["ret_15m_after_fill"] > 0)
    _eval("ret_30m_after_fill>0", df["ret_30m_after_fill"] > 0)
    _eval("h1_ret>0 (path)", df["h1_ret_pct"] > 0)
    _eval("prev_low_atr & wide_or", (df["prev_atr_rank20"] <= 50) & (df["or_width_pct"] >= med_or))
    _eval("prev_low_atr & ret15m>0", (df["prev_atr_rank20"] <= 50) & (df["ret_15m_after_fill"] > 0))
    _eval("aligned & ret15m>0", df["trend_aligned_at_entry"] & (df["ret_15m_after_fill"] > 0))

    # 区分力：fat vs fake 均值差
    diffs = []
    for c in compare_cols:
        if c not in fat.columns or fat[c].isna().all() or fake[c].isna().all():
            continue
        fv, xv = float(fat[c].mean()), float(fake[c].mean())
        diffs.append({"feature": c, "true_fat_avg": round(fv, 3), "fake_sl_avg": round(xv, 3), "delta": round(fv - xv, 3)})
    diffs.sort(key=lambda x: abs(x["delta"]), reverse=True)

    findings = []
    if diffs:
        top = diffs[0]
        findings.append(f"最大均值差: {top['feature']} fat={top['true_fat_avg']} vs fake={top['fake_sl_avg']}")
    if stats.get("true_fat", {}).get("prev_atr_rank20") is not None:
        findings.append(
            f"大赢前日ATR分位 avg={stats['true_fat']['prev_atr_rank20']} vs 止损 avg={stats.get('fake_sl',{}).get('prev_atr_rank20')}"
        )
    if stats.get("true_fat", {}).get("h1_ret_pct") is not None:
        findings.append(
            f"入场后1h: 大赢 h1_ret avg={stats['true_fat']['h1_ret_pct']}% vs 止损 {stats.get('fake_sl',{}).get('h1_ret_pct')}%"
        )

    payload = {
        "symbol": "COINUSDT",
        "strategy": "OR10_3pct",
        "labels": {"true_fat": ">=5R", "fake_sl": "stop loss", "true_small": "win not fat", "fake_eod": "EOD loss"},
        "counts": df["label"].value_counts().to_dict(),
        "group_means": stats,
        "fat_vs_fake_mean_diff": diffs[:12],
        "rule_scan": sorted(rules, key=lambda x: x["fat_rate"], reverse=True),
        "findings": findings,
        "trades": df.to_dict(orient="records"),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps({"counts": payload["counts"], "findings": findings, "top_diffs": diffs[:8], "top_rules": payload["rule_scan"][:6]}, indent=2, ensure_ascii=False))
    print(f"\nWrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
