#!/usr/bin/env python3
"""单标 ORB 画像：先历史/session 统计，再 baseline 交易分解，最后参数验证。

用法:
  python tools/orb/v2/explore_symbol_profile.py CRCL
  python tools/orb/v2/explore_symbol_profile.py COIN --from 2026-02-09 --to 2026-06-24
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

logging.getLogger("orb").setLevel(logging.ERROR)

from env_loader import load_env_oi  # noqa: E402

from orb.core.config import OrbConfig  # noqa: E402
from orb.core.kline_cache import load_klines, norm_symbol  # noqa: E402
from orb.core.session import (  # noqa: E402
    compute_opening_range,
    session_anchor_ms,
    session_close_ms,
    session_slice,
)
from orb.ml.gate import LiveGateConfig, gate_with_ml_bypass  # noqa: E402
from orb.v2.paths import resolve_gate_config_path  # noqa: E402
from orb.v2.robots import init_robot_wallets  # noqa: E402
from tools.orb.ml.eval_live_gate import _ml_cfg  # noqa: E402
from tools.orb.v2.backtest_universe import filter_backtest_sessions_with_atr, universe_session_dates  # noqa: E402
from tools.orb.v2.batch_symbol_sim import _run_one  # noqa: E402
from tools.orb.v2.sim_live_session import simulate_live_sessions  # noqa: E402

LO_DEFAULT, HI_DEFAULT = "2026-02-09", "2026-06-24"
EQ, FEE = 1000.0, 4.0


def _dates(sym: str, cfg: OrbConfig, lo: str, hi: str) -> List[str]:
    raw = [d for d in universe_session_dates([sym], cfg) if lo <= d <= hi]
    return filter_backtest_sessions_with_atr(raw, [sym], cfg)


def _run_bt(
    sym: str,
    dates: List[str],
    *,
    or_minutes: int,
    risk_pct: float,
    trade_window_minutes: int = 0,
    min_or_width_pct: float = 0.0,
) -> Dict[str, Any]:
    gate = gate_with_ml_bypass(LiveGateConfig.from_json(Path(resolve_gate_config_path())))
    cfg = _ml_cfg(compound_per_symbol=True, respect_env_filters=False)
    cfg.or_minutes = int(or_minutes)
    cfg.risk_pct = float(risk_pct)
    cfg.trade_window_minutes = int(trade_window_minutes)
    cfg.min_or_width_pct = float(min_or_width_pct)
    cfg.macro_filter = False
    cfg.exit_mode = "eod"
    cfg.sl_mode = "atr_pct"
    wallets = init_robot_wallets(count=1, equity_usdt=EQ)
    days = simulate_live_sessions(
        dates,
        [sym],
        gate=gate,
        ranker=None,
        cfg=cfg,
        robot_wallets=wallets,
        respect_env_filters=False,
        fee_bps_per_side=FEE,
        entry_fill="preplace_stop",
        ml_enabled=False,
    )
    trades = [t for d in days for t in (d.get("trades") or [])]
    wallet_net = round(float(wallets[0]) - EQ, 2)
    return {
        "or_minutes": or_minutes,
        "risk_pct": risk_pct,
        "trade_window_minutes": trade_window_minutes,
        "min_or_width_pct": min_or_width_pct,
        "opens": len(trades),
        "wallet_net_usdt": wallet_net,
        "end_wallet_usdt": round(float(wallets[0]), 2),
        "win_rate": round(sum(1 for t in trades if float(t.get("pnl_usdt") or 0) > 0) / len(trades) * 100, 1)
        if trades
        else 0.0,
        "trades": trades,
    }


def _session_stats(df5: pd.DataFrame, dates: List[str], cfg: OrbConfig, or_minutes: int) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for d in dates:
        ts = pd.Timestamp(d + " 12:00:00", tz=cfg.session_tz)
        anchor = session_anchor_ms(
            int(ts.value // 1_000_000),
            tz=cfg.session_tz,
            session_open_time=cfg.session_open_time,
        )
        close_ms = session_close_ms(anchor, tz=cfg.session_tz, session_close_time=cfg.session_close_time)
        if close_ms is None:
            continue
        sess = session_slice(
            df5, close_ms - 60_000, tz=cfg.session_tz, session_open_time=cfg.session_open_time
        )
        if len(sess) < 10:
            continue
        open_p = float(sess.iloc[0]["open"])
        close_p = float(sess.iloc[-1]["close"])
        hi, lo = float(sess["high"].max()), float(sess["low"].min())
        pack = compute_opening_range(
            sess,
            or_minutes=or_minutes,
            bar_step_ms=300_000,
            asof_open_ms=close_ms - 60_000,
            tz=cfg.session_tz,
            session_open_time=cfg.session_open_time,
        )
        or_w = float(pack["or_width_pct"]) if pack else np.nan
        morning = sess.iloc[: min(24, len(sess))]
        morn_ret = (float(morning.iloc[-1]["close"]) - open_p) / open_p * 100
        rows.append(
            {
                "date": d,
                "ret_pct": (close_p - open_p) / open_p * 100,
                "range_pct": (hi - lo) / open_p * 100,
                "or_width_pct": or_w,
                "morn_ret": morn_ret,
            }
        )
    return pd.DataFrame(rows)


def _enrich_trades(trades: List[Dict[str, Any]], df5: pd.DataFrame, cfg: OrbConfig, or_minutes: int) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for t in trades:
        d = str(t.get("session_date") or "")
        side = str(t.get("side") or "")
        entry = float(t.get("entry") or 0)
        sl = float(t.get("sl") or t.get("sl_price") or 0)
        pnl = float(t.get("pnl_usdt") or 0)
        outcome = str(t.get("outcome") or "")
        win = outcome == "session_close" or pnl > 0 and outcome not in ("loss", "sl")
        if outcome == "session_close":
            win = True
        elif outcome in ("loss", "sl", "stop"):
            win = False

        ts = pd.Timestamp(d + " 12:00:00", tz=cfg.session_tz)
        anchor = session_anchor_ms(
            int(ts.value // 1_000_000),
            tz=cfg.session_tz,
            session_open_time=cfg.session_open_time,
        )
        or_end = anchor + or_minutes * 60_000
        fill_ms = int(t.get("fill_bar_open_ms") or t.get("scan_open_ms") or 0)
        mins_after_or = max(0, (fill_ms - or_end) / 60_000) if fill_ms > or_end else 0.0

        close_ms = session_close_ms(anchor, tz=cfg.session_tz, session_close_time=cfg.session_close_time)
        sess = session_slice(df5, close_ms - 60_000, tz=cfg.session_tz, session_open_time=cfg.session_open_time)
        or_w = np.nan
        day_ret = np.nan
        if not sess.empty and close_ms:
            open_p = float(sess.iloc[0]["open"])
            close_p = float(sess.iloc[-1]["close"])
            day_ret = (close_p - open_p) / open_p * 100
            pack = compute_opening_range(
                sess,
                or_minutes=or_minutes,
                bar_step_ms=300_000,
                asof_open_ms=or_end,
                tz=cfg.session_tz,
                session_open_time=cfg.session_open_time,
            )
            if pack:
                or_w = float(pack["or_width_pct"])

        risk_dist = abs(entry - sl) / entry * 100 if entry > 0 and sl > 0 else np.nan
        pnl_r = pnl / (EQ * cfg.risk_pct) if cfg.risk_pct > 0 else np.nan

        mfe, mae, eod_ret, h1_ret = np.nan, np.nan, np.nan, np.nan
        if fill_ms > 0 and close_ms:
            path = df5[(df5["open_time"] >= fill_ms) & (df5["open_time"] <= close_ms)]
            if not path.empty:
                hi = path["high"].astype(float)
                lo = path["low"].astype(float)
                cl = path["close"].astype(float)
                if "LONG" in side.upper():
                    mfe = (hi.max() - entry) / entry * 100
                    mae = (entry - lo.min()) / entry * 100
                    eod_ret = (float(cl.iloc[-1]) - entry) / entry * 100
                else:
                    mfe = (entry - lo.min()) / entry * 100
                    mae = (hi.max() - entry) / entry * 100
                    eod_ret = (entry - float(cl.iloc[-1])) / entry * 100
                end1h = fill_ms + 60 * 60_000
                h1 = path[path["open_time"] <= end1h]
                if len(h1) >= 2:
                    p1 = float(h1.iloc[-1]["close"])
                    h1_ret = (p1 - entry) / entry * 100 if "LONG" in side.upper() else (entry - p1) / entry * 100

        rows.append(
            {
                "session_date": d,
                "side": side,
                "outcome": outcome,
                "win": win,
                "pnl_usdt": pnl,
                "pnl_r": pnl_r,
                "or_width_pct": or_w,
                "minutes_after_or": mins_after_or,
                "day_ret_pct": day_ret,
                "risk_dist_pct": risk_dist,
                "mfe_pct": mfe,
                "mae_pct": mae,
                "eod_ret_pct": eod_ret,
                "h1_ret_pct": h1_ret,
            }
        )
    return pd.DataFrame(rows)


def _bucket_table(trades: pd.DataFrame, col: str, bins: List[float], labels: List[str]) -> List[Dict[str, Any]]:
    if trades.empty or col not in trades.columns:
        return []
    t = trades.copy()
    t["_b"] = pd.cut(t[col], bins=bins, labels=labels)
    out: List[Dict[str, Any]] = []
    for b, g in t.groupby("_b", observed=True):
        if len(g) == 0:
            continue
        wins = g[g["win"]]
        out.append(
            {
                "bucket": str(b),
                "n": int(len(g)),
                "win_rate_pct": round(float(g["win"].mean()) * 100, 1),
                "pnl_usdt": round(float(g["pnl_usdt"].sum()), 2),
                "avg_r": round(float(wins["pnl_r"].mean()), 2) if len(wins) else 0.0,
            }
        )
    return out


def _summarize_sessions(sess: pd.DataFrame) -> Dict[str, Any]:
    if sess.empty:
        return {}
    up = int((sess["ret_pct"] > 0.5).sum())
    dn = int((sess["ret_pct"] < -0.5).sum())
    return {
        "sessions": int(len(sess)),
        "avg_range_pct": round(float(sess["range_pct"].mean()), 2),
        "median_range_pct": round(float(sess["range_pct"].median()), 2),
        "avg_or_width_pct": round(float(sess["or_width_pct"].mean()), 2),
        "median_or_width_pct": round(float(sess["or_width_pct"].median()), 2),
        "avg_day_ret_pct": round(float(sess["ret_pct"].mean()), 2),
        "trend_up_days": up,
        "trend_down_days": dn,
        "chop_days": int(len(sess) - up - dn),
        "avg_morning_ret_pct": round(float(sess["morn_ret"].mean()), 2),
    }


def _summarize_trades(trades: pd.DataFrame) -> Dict[str, Any]:
    if trades.empty:
        return {"opens": 0}
    wins = trades[trades["win"]]
    losses = trades[~trades["win"]]
    big = wins[wins["pnl_r"] >= 5] if "pnl_r" in wins.columns else pd.DataFrame()
    return {
        "opens": int(len(trades)),
        "eod_wins": int(len(wins)),
        "sl_losses": int(len(losses)),
        "win_rate_pct": round(float(trades["win"].mean()) * 100, 1),
        "eod_pnl_usdt": round(float(wins["pnl_usdt"].sum()), 2),
        "sl_pnl_usdt": round(float(losses["pnl_usdt"].sum()), 2),
        "avg_eod_r": round(float(wins["pnl_r"].mean()), 2) if len(wins) else 0.0,
        "big_wins_5r": int(len(big)),
        "avg_eod_mfe_pct": round(float(wins["mfe_pct"].mean()), 2) if len(wins) and wins["mfe_pct"].notna().any() else None,
        "avg_eod_h1_ret_pct": round(float(wins["h1_ret_pct"].mean()), 2)
        if len(wins) and wins["h1_ret_pct"].notna().any()
        else None,
        "eod_h1_still_positive_pct": round(float((wins["h1_ret_pct"] > 0).mean()) * 100, 1)
        if len(wins) and wins["h1_ret_pct"].notna().any()
        else None,
    }


def _derive_personality(sess: Dict[str, Any], trade_sum: Dict[str, Any]) -> List[str]:
    notes: List[str] = []
    if sess.get("avg_range_pct", 0) >= 5:
        notes.append("高波动：日均振幅偏大，ORB 止损距离宽")
    elif sess.get("avg_range_pct", 0) <= 3:
        notes.append("中低波动：振幅小于 COIN 典型水平")
    wr = trade_sum.get("win_rate_pct", 0)
    if wr <= 15:
        notes.append("低胜率 fat-tail：靠少数 EOD 大赢家贡献主要利润")
    elif wr >= 25:
        notes.append("胜率相对更高：策略更依赖稳定小赢而非极端 tail")
    if trade_sum.get("big_wins_5r", 0) >= 5:
        notes.append(f"存在 {trade_sum['big_wins_5r']} 笔 >=5R 大赢，适合 EOD 持有")
    if trade_sum.get("avg_eod_h1_ret_pct") is not None and trade_sum.get("eod_h1_still_positive_pct", 0) >= 80:
        notes.append("EOD 赢家入场后 1h 几乎不回吐，early_exit 可能伤 edge")
    return notes


def explore(sym_raw: str, lo: str, hi: str) -> Dict[str, Any]:
    load_env_oi()
    sym = norm_symbol(sym_raw)
    tag = sym.replace("USDT", "")
    cfg = _ml_cfg(compound_per_symbol=True, respect_env_filters=False)
    cfg.macro_filter = False
    dates = _dates(sym, cfg, lo, hi)
    df5 = load_klines(sym, "5m")

    print(f"\n{'='*70}\n{tag} ORB profile | {len(dates)} ATR sessions | {lo}..{hi}\n{'='*70}")

    # [1] Session stats for OR5/10/15
    sess_by_or: Dict[str, Any] = {}
    for or_m in (5, 10, 15):
        sess_df = _session_stats(df5, dates, cfg, or_m)
        sess_by_or[str(or_m)] = _summarize_sessions(sess_df)
        s = sess_by_or[str(or_m)]
        print(
            f"[1] OR{or_m} session: range={s.get('avg_range_pct')}% or_w={s.get('avg_or_width_pct')}% "
            f"trend up/down/chop={s.get('trend_up_days')}/{s.get('trend_down_days')}/{s.get('chop_days')}"
        )

    # [2] Baseline OR5/10/15 @ 1% risk
    or_baselines: List[Dict[str, Any]] = []
    print("\n[2] Baseline preplace @ 1% risk")
    for or_m in (5, 10, 15):
        r = _run_bt(sym, dates, or_minutes=or_m, risk_pct=0.01)
        tdf = _enrich_trades(r["trades"], df5, cfg, or_m)
        row = {
            "or_minutes": or_m,
            "wallet_net_usdt": r["wallet_net_usdt"],
            "opens": r["opens"],
            "win_rate_pct": r["win_rate"],
            "trade_summary": _summarize_trades(tdf),
        }
        or_baselines.append(row)
        print(
            f"  OR{or_m}: net={r['wallet_net_usdt']:+.0f}U opens={r['opens']} WR={r['win_rate']:.0f}% "
            f"big5R={row['trade_summary'].get('big_wins_5r', 0)}"
        )

    best_or = max(or_baselines, key=lambda x: x["wallet_net_usdt"])
    primary_or = int(best_or["or_minutes"])
    print(f"\n  -> best OR period @ 1%: OR{primary_or}")

    # [3] Deep dive on best OR baseline trades
    base = _run_bt(sym, dates, or_minutes=primary_or, risk_pct=0.01)
    cfg.or_minutes = primary_or
    trades_df = _enrich_trades(base["trades"], df5, cfg, primary_or)
    trade_sum = _summarize_trades(trades_df)
    personality = _derive_personality(sess_by_or[str(primary_or)], trade_sum)

    print(f"\n[3] Trade anatomy (OR{primary_or} baseline)")
    print(
        f"  EOD={trade_sum.get('eod_wins')} SL={trade_sum.get('sl_losses')} "
        f"EOD PnL={trade_sum.get('eod_pnl_usdt'):+.0f} SL PnL={trade_sum.get('sl_pnl_usdt'):+.0f} "
        f"avg EOD R={trade_sum.get('avg_eod_r')}"
    )
    for n in personality:
        print(f"  * {n}")

    or_buckets = _bucket_table(
        trades_df,
        "or_width_pct",
        [0, 1.5, 2.5, 3.5, 5, 100],
        ["<1.5%", "1.5-2.5%", "2.5-3.5%", "3.5-5%", ">5%"],
    )
    print("\n[4] OR width buckets (baseline trades)")
    for b in or_buckets:
        print(f"  {b['bucket']}: n={b['n']} WR={b['win_rate_pct']}% PnL={b['pnl_usdt']:+.0f}U")

    timing_buckets = _bucket_table(
        trades_df,
        "minutes_after_or",
        [-1, 15, 30, 60, 90, 9999],
        ["<=15m", "15-30m", "30-60m", "60-90m", ">90m"],
    )
    print("\n[5] Entry timing after OR")
    for b in timing_buckets:
        print(f"  {b['bucket']}: n={b['n']} WR={b['win_rate_pct']}% PnL={b['pnl_usdt']:+.0f}U")

    # [6] Config validation from analysis hints
    print("\n[6] Config validation (guided by profile)")
    candidates: List[Tuple[str, Dict[str, Any]]] = [
        ("baseline", {"or_minutes": primary_or, "risk_pct": 0.01, "trade_window_minutes": 0}),
        ("tw60", {"or_minutes": primary_or, "risk_pct": 0.01, "trade_window_minutes": 60}),
        ("tw90", {"or_minutes": primary_or, "risk_pct": 0.01, "trade_window_minutes": 90}),
        ("tw120", {"or_minutes": primary_or, "risk_pct": 0.01, "trade_window_minutes": 120}),
        ("min_or2.0", {"or_minutes": primary_or, "risk_pct": 0.01, "trade_window_minutes": 0, "min_or_width_pct": 2.0}),
        ("min_or2.5", {"or_minutes": primary_or, "risk_pct": 0.01, "trade_window_minutes": 0, "min_or_width_pct": 2.5}),
    ]
    # pick best tw from timing if late entries dominate PnL
    late_pnl = sum(b["pnl_usdt"] for b in timing_buckets if b["bucket"] in (">90m", "60-90m"))
    early_pnl = sum(b["pnl_usdt"] for b in timing_buckets if b["bucket"] in ("<=15m", "15-30m"))
    best_tw_hint = 90 if late_pnl > early_pnl else 0

    for risk in (0.015, 0.02, 0.025, 0.03):
        candidates.append(
            (
                f"risk{risk*100:.1f}_tw{best_tw_hint}",
                {
                    "or_minutes": primary_or,
                    "risk_pct": risk,
                    "trade_window_minutes": best_tw_hint,
                },
            )
        )

    validated: List[Dict[str, Any]] = []
    for label, kw in candidates:
        r = _run_bt(sym, dates, **kw)
        validated.append({"label": label, **kw, **{k: r[k] for k in ("wallet_net_usdt", "opens", "win_rate")}})
    validated.sort(key=lambda x: x["wallet_net_usdt"], reverse=True)
    for v in validated[:8]:
        print(
            f"  {v['label']:<14} OR{v['or_minutes']} risk={v['risk_pct']*100:.1f}% tw={v['trade_window_minutes']} "
            f"net={v['wallet_net_usdt']:+.0f}U WR={v['win_rate']:.0f}%"
        )

    recommended = validated[0]
    # prefer moderate risk if top is only slightly better at high risk
    moderate = [v for v in validated if v["risk_pct"] <= 0.025]
    if moderate and moderate[0]["wallet_net_usdt"] >= validated[0]["wallet_net_usdt"] * 0.85:
        recommended = moderate[0]

    report = {
        "symbol": sym,
        "tag": tag,
        "date_range": {"from": lo, "to": hi, "sessions_atr": len(dates)},
        "session_stats_by_or": sess_by_or,
        "or_baselines_1pct": or_baselines,
        "primary_or_minutes": primary_or,
        "personality": personality,
        "trade_summary_baseline": trade_sum,
        "or_width_buckets": or_buckets,
        "entry_timing_buckets": timing_buckets,
        "config_validation": validated,
        "recommended": recommended,
        "target_4000_usdt": bool(recommended["wallet_net_usdt"] >= 4000),
    }

    out_dir = ROOT / "output" / "orb" / "v2" / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / f"{tag.lower()}_profile.json"
    out_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    trades_df.to_csv(out_dir / f"{tag.lower()}_baseline_trades.csv", index=False)
    print(f"\njson -> {out_json}")
    print(
        f"RECOMMEND: OR{recommended['or_minutes']} risk={recommended['risk_pct']*100:.1f}% "
        f"tw={recommended['trade_window_minutes']} -> {recommended['wallet_net_usdt']:+.0f}U"
    )
    return report


def write_symbol_config(report: Dict[str, Any]) -> Path:
    tag = str(report["tag"])
    rec = report["recommended"]
    cfg_dir = ROOT / "config" / "orb" / tag
    cfg_dir.mkdir(parents=True, exist_ok=True)

    (cfg_dir / "symbols.txt").write_text(
        f"# {tag} 单标\n{tag}\n",
        encoding="utf-8",
    )

    env_lines = [
        f"# {tag} — data-driven profile",
        f"# OR{rec['or_minutes']} + {rec['risk_pct']*100:.1f}% risk + tw{rec['trade_window_minutes']}m",
        f"# wallet net {rec['wallet_net_usdt']:+.0f}U | see output/orb/v2/eval/{tag.lower()}_profile.json",
        "",
        "ORB_MARKET=us_equity",
        f"ORB_SYMBOLS={report['symbol']}",
        "",
        f"ORB_OR_MINUTES={rec['or_minutes']}",
        f"ORB_RISK_PCT={rec['risk_pct']}",
        f"ORB_TRADE_WINDOW_MINUTES={rec['trade_window_minutes']}",
        "ORB_ONE_TRADE_PER_SESSION=1",
        "",
        "ORB_EXIT_MODE=eod",
        "ORB_SL_MODE=atr_pct",
        "ORB_ATR_PERIOD=14",
        "ORB_ATR_SL_FRACTION=0.05",
        "",
        "ORB_SIGNAL_INTERVAL=5m",
        "ORB_MACRO_FILTER=0",
        "ORB_SYMBOL_BOT_EQUITY_USDT=1000",
        "ORB_V2_ROBOT_EQUITY=1000",
        "ORB_V2_GATE_ML=0",
        "",
    ]
    if rec.get("min_or_width_pct"):
        env_lines.insert(-1, f"ORB_MIN_OR_WIDTH_PCT={rec['min_or_width_pct']}")
    (cfg_dir / "strategy.env").write_text("\n".join(env_lines), encoding="utf-8")

    alts = [v for v in report["config_validation"][:5] if v["label"] != rec.get("label")]
    config_json = {
        "symbol": report["symbol"],
        "label": rec.get("label"),
        "personality": report.get("personality"),
        "target_4000_usdt": report.get("target_4000_usdt"),
        "backtest": {
            "from": report["date_range"]["from"],
            "to": report["date_range"]["to"],
            "sessions_atr": report["date_range"]["sessions_atr"],
            "wallet_net_usdt": rec["wallet_net_usdt"],
            "opens": rec["opens"],
            "win_rate_pct": rec["win_rate"],
        },
        "strategy": {k: rec[k] for k in ("or_minutes", "risk_pct", "trade_window_minutes") if k in rec},
        "session_stats": report["session_stats_by_or"].get(str(rec["or_minutes"]), {}),
        "trade_summary": report.get("trade_summary_baseline"),
        "alternatives": alts,
    }
    (cfg_dir / "config.json").write_text(json.dumps(config_json, indent=2, ensure_ascii=False), encoding="utf-8")
    return cfg_dir


def main() -> int:
    ap = argparse.ArgumentParser(description="Symbol ORB profile from historical data")
    ap.add_argument("symbol", help="e.g. CRCL, COIN, HOOD")
    ap.add_argument("--from-date", default=LO_DEFAULT)
    ap.add_argument("--to-date", default=HI_DEFAULT)
    ap.add_argument("--write-config", action="store_true", default=True)
    ap.add_argument("--no-write-config", action="store_false", dest="write_config")
    args = ap.parse_args()
    t0 = time.time()
    report = explore(args.symbol.upper(), args.from_date, args.to_date)
    if args.write_config:
        p = write_symbol_config(report)
        print(f"config -> {p}")
    print(f"elapsed {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
