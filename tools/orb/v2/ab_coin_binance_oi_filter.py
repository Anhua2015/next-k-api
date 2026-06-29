#!/usr/bin/env python3
"""COIN OR10：Binance OI 前置 filter 回测 + posthoc 扫描。"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi  # noqa: E402
from orb.core.config import OrbConfig  # noqa: E402
from orb.core.session import session_anchor_ms  # noqa: E402
from orb.ml.gate import LiveGateConfig, gate_with_ml_bypass  # noqa: E402
from orb.v2.paths import resolve_gate_config_path  # noqa: E402
from orb.v2.robots import init_robot_wallets  # noqa: E402
from tools.orb.v2.backtest_universe import filter_backtest_sessions_with_atr, universe_session_dates  # noqa: E402
from tools.orb.v2.sim_live_session import DEFAULT_FEE_BPS_PER_SIDE, simulate_live_sessions  # noqa: E402

SYMBOL = "COINUSDT"
FROM_DATE = "2026-02-09"
TO_DATE = "2026-06-24"
RISK_PCT = 0.03
TRADES_CSV = ROOT / "output" / "orb" / "v2" / "eval" / "coin_or10_3pct_tw0.trades.csv"
OUT_JSON = ROOT / "output" / "orb" / "v2" / "eval" / "coin_or10_binance_oi_filter.json"
FAPI_DATA = "https://fapi.binance.com/futures/data"


def _coin_cfg() -> OrbConfig:
    import os

    os.environ.setdefault("ORB_MARKET", "us_equity")
    os.environ["ORB_OR_MINUTES"] = "10"
    os.environ["ORB_RISK_PCT"] = "0.03"
    os.environ["ORB_ONE_TRADE_PER_SESSION"] = "1"
    os.environ["ORB_EXIT_MODE"] = "eod"
    os.environ["ORB_SL_MODE"] = "atr_pct"
    os.environ["ORB_ATR_PERIOD"] = "14"
    os.environ["ORB_ATR_SL_FRACTION"] = "0.05"
    os.environ["ORB_SIGNAL_INTERVAL"] = "5m"
    os.environ["ORB_MACRO_FILTER"] = "0"
    os.environ["ORB_V2_ROBOT_RESET_CAP"] = "0"
    os.environ["ORB_V2_GATE_ML"] = "0"
    return OrbConfig.from_env()


def fetch_oi_hist(symbol: str, *, period: str = "1h", limit: int = 500) -> pd.DataFrame:
    rows: List[dict] = []
    end_ms: Optional[int] = None
    while len(rows) < 2000:
        params: Dict[str, Any] = {"symbol": symbol, "period": period, "limit": min(limit, 500)}
        if end_ms is not None:
            params["endTime"] = end_ms
        r = requests.get(f"{FAPI_DATA}/openInterestHist", params=params, timeout=30)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        oldest = min(int(x["timestamp"]) for x in batch)
        if len(batch) < 500:
            break
        end_ms = oldest - 1
        if oldest <= pd.Timestamp(FROM_DATE, tz="America/New_York").value // 1_000_000 - 86400_000 * 30:
            break
        time.sleep(0.2)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).drop_duplicates("timestamp").sort_values("timestamp")
    df["oi"] = df["sumOpenInterest"].astype(float)
    df["oi_usd"] = df["sumOpenInterestValue"].astype(float)
    return df


def fetch_taker_ratio(symbol: str, *, period: str = "1h", limit: int = 500) -> pd.DataFrame:
    r = requests.get(
        f"{FAPI_DATA}/takerlongshortRatio",
        params={"symbol": symbol, "period": period, "limit": limit},
        timeout=30,
    )
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    if df.empty:
        return df
    df["timestamp"] = df["timestamp"].astype(int)
    df["ratio"] = df["buySellRatio"].astype(float)
    return df.sort_values("timestamp")


def build_session_features(
    dates: List[str],
    oi: pd.DataFrame,
    taker: pd.DataFrame,
    cfg: OrbConfig,
) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for day in dates:
        ts = pd.Timestamp(f"{day} 12:00:00", tz=cfg.session_tz)
        anchor = session_anchor_ms(int(ts.value // 1_000_000), tz=cfg.session_tz, session_open_time=cfg.session_open_time)
        oi_before = oi[oi["timestamp"] <= anchor]
        if oi_before.empty:
            continue
        cur = oi_before.iloc[-1]
        oi_val = float(cur["oi_usd"])
        hist = oi_before.tail(24)
        rank = float((hist["oi_usd"] < oi_val).mean() * 100) if len(hist) >= 5 else np.nan
        oi_24h = oi_before.iloc[-25]["oi_usd"] if len(oi_before) >= 25 else np.nan
        oi_12h = oi_before.iloc[-13]["oi_usd"] if len(oi_before) >= 13 else np.nan
        oi_chg_24h = (oi_val - float(oi_24h)) / float(oi_24h) * 100 if pd.notna(oi_24h) and float(oi_24h) > 0 else np.nan
        oi_chg_12h = (oi_val - float(oi_12h)) / float(oi_12h) * 100 if pd.notna(oi_12h) and float(oi_12h) > 0 else np.nan

        taker_before = taker[taker["timestamp"] <= anchor]
        taker_ratio = float(taker_before.iloc[-1]["ratio"]) if not taker_before.empty else np.nan

        rows.append(
            {
                "session_date": day,
                "oi_usd": round(oi_val, 2),
                "oi_rank24": round(rank, 1) if not np.isnan(rank) else None,
                "oi_chg_24h_pct": round(oi_chg_24h, 3) if not np.isnan(oi_chg_24h) else None,
                "oi_chg_12h_pct": round(oi_chg_12h, 3) if not np.isnan(oi_chg_12h) else None,
                "oi_expanding_24h": bool(pd.notna(oi_chg_24h) and oi_chg_24h > 0),
                "oi_contracting_24h": bool(pd.notna(oi_chg_24h) and oi_chg_24h < 0),
                "taker_ratio_1h": round(taker_ratio, 4) if not np.isnan(taker_ratio) else None,
            }
        )
    return pd.DataFrame(rows)


def _posthoc(trades: pd.DataFrame, feat: pd.DataFrame) -> List[Dict[str, Any]]:
    m = trades.merge(feat, on="session_date", how="inner")
    m["pnl_r"] = m["pnl_usdt"] / (m["wallet_before"] * RISK_PCT)
    m["is_big5r"] = m["pnl_r"] >= 5
    base_net = float(m["pnl_usdt"].sum())
    median_oi_chg = float(m["oi_chg_24h_pct"].median())

    filters: List[tuple[str, Callable[[pd.Series], bool]]] = [
        ("baseline_all", lambda r: True),
        ("oi_rank24<=50", lambda r: float(r["oi_rank24"]) <= 50),
        ("oi_rank24<=40", lambda r: float(r["oi_rank24"]) <= 40),
        ("oi_expanding_24h", lambda r: bool(r["oi_expanding_24h"])),
        ("oi_contracting_24h", lambda r: bool(r["oi_contracting_24h"])),
        ("oi_chg_24h>0", lambda r: float(r["oi_chg_24h_pct"]) > 0),
        ("oi_chg_24h<0", lambda r: float(r["oi_chg_24h_pct"]) < 0),
        (f"oi_chg_24h<{median_oi_chg:.2f}%", lambda r: float(r["oi_chg_24h_pct"]) < median_oi_chg),
        ("taker_ratio>1 (偏多)", lambda r: float(r["taker_ratio_1h"]) > 1.0),
        ("taker_ratio<1 (偏空)", lambda r: float(r["taker_ratio_1h"]) < 1.0),
        ("oi_expanding & rank<=50", lambda r: bool(r["oi_expanding_24h"]) and float(r["oi_rank24"]) <= 50),
    ]
    out: List[Dict[str, Any]] = []
    for name, pred in filters:
        sub = m[m.apply(pred, axis=1)]
        if sub.empty:
            continue
        wins = int((sub["pnl_usdt"] > 0).sum())
        out.append(
            {
                "tag": name,
                "n": int(len(sub)),
                "win_rate": round(wins / len(sub) * 100, 1),
                "net_pnl_usdt": round(float(sub["pnl_usdt"].sum()), 2),
                "avg_pnl": round(float(sub["pnl_usdt"].mean()), 2),
                "big5r": int(sub["is_big5r"].sum()),
                "vs_baseline_net": round(float(sub["pnl_usdt"].sum()) - base_net, 2),
            }
        )
    return out


def _run_sim(dates: List[str], cfg: OrbConfig) -> Dict[str, Any]:
    gate = gate_with_ml_bypass(LiveGateConfig.from_json(Path(resolve_gate_config_path())))
    wallets = init_robot_wallets(count=1, equity_usdt=1000.0)
    t0 = time.time()
    days = simulate_live_sessions(
        dates,
        [SYMBOL],
        gate=gate,
        ranker=None,
        cfg=cfg,
        robot_wallets=wallets,
        respect_env_filters=False,
        fee_bps_per_side=DEFAULT_FEE_BPS_PER_SIDE,
        entry_fill="preplace_stop",
        ml_enabled=False,
        atr_daily_source="binance_1d",
    )
    trades = [t for d in days for t in (d.get("trades") or [])]
    wins = sum(1 for t in trades if float(t.get("pnl_usdt") or 0) > 0)
    big5 = sum(
        1
        for t in trades
        if float(t.get("pnl_usdt") or 0) > 0
        and float(t.get("pnl_usdt") or 0) / (float(t.get("wallet_before") or 1) * RISK_PCT) >= 5
    )
    net = round(sum(float(d.get("net_pnl_usdt") or 0) for d in days), 2)
    return {
        "sessions": len(dates),
        "opens": len(trades),
        "win_rate": round(wins / len(trades) * 100, 1) if trades else 0.0,
        "big5r": big5,
        "net_pnl_usdt": net,
        "end_wallet": round(float(wallets[0]), 2),
        "elapsed_sec": round(time.time() - t0, 1),
    }


def main() -> int:
    load_env_oi()
    cfg = _coin_cfg()
    print(f"Fetching Binance OI for {SYMBOL} ...", flush=True)
    oi = fetch_oi_hist(SYMBOL, period="1h")
    taker = fetch_taker_ratio(SYMBOL, period="1h")
    if oi.empty:
        print("No OI data")
        return 1

    raw = [d for d in universe_session_dates([SYMBOL], cfg) if FROM_DATE <= d <= TO_DATE]
    all_dates = filter_backtest_sessions_with_atr(raw, [SYMBOL], cfg, atr_daily_source="binance_1d")
    feat = build_session_features(all_dates, oi, taker, cfg)

    trades = pd.read_csv(TRADES_CSV)
    posthoc = _posthoc(trades, feat)

    # full sim for baseline + best-looking posthoc filters
    feat_map = {str(r["session_date"]): r for _, r in feat.iterrows()}
    sim_variants = [
        ("sim_baseline", "无 OI filter", all_dates),
        (
            "sim_oi_expanding",
            "前24h OI 扩张（oi_usd 上升）",
            [d for d in all_dates if d in feat_map and feat_map[d]["oi_expanding_24h"]],
        ),
        (
            "sim_oi_contracting",
            "前24h OI 收缩",
            [d for d in all_dates if d in feat_map and feat_map[d]["oi_contracting_24h"]],
        ),
        (
            "sim_oi_rank50",
            "OI 近24h 分位≤50",
            [d for d in all_dates if d in feat_map and float(feat_map[d]["oi_rank24"]) <= 50],
        ),
    ]
    sim_results: List[Dict[str, Any]] = []
    for tag, label, dates in sim_variants:
        print(f"[{tag}] sessions={len(dates)} ...", flush=True)
        row = {"tag": tag, "label": label, **_run_sim(dates, cfg)}
        sim_results.append(row)
        print(f"  net={row['net_pnl_usdt']:+.1f}U opens={row['opens']} big5R={row['big5r']}", flush=True)

    # big5r day OI stats
    m = trades.merge(feat, on="session_date", how="inner")
    m["pnl_r"] = m["pnl_usdt"] / (m["wallet_before"] * RISK_PCT)
    big = m[m["pnl_r"] >= 5]
    loss = m[m["pnl_usdt"] <= 0]

    payload = {
        "symbol": SYMBOL,
        "from_date": FROM_DATE,
        "to_date": TO_DATE,
        "oi_source": "binance openInterestHist 1h sumOpenInterestValue",
        "taker_source": "binance takerlongshortRatio 1h",
        "posthoc_on_baseline_trades": posthoc,
        "full_sim": sim_results,
        "group_stats": {
            "big5r_avg_oi_chg_24h": round(float(big["oi_chg_24h_pct"].mean()), 3) if len(big) else None,
            "loss_avg_oi_chg_24h": round(float(loss["oi_chg_24h_pct"].mean()), 3) if len(loss) else None,
            "big5r_avg_oi_rank24": round(float(big["oi_rank24"].mean()), 1) if len(big) else None,
            "loss_avg_oi_rank24": round(float(loss["oi_rank24"].mean()), 1) if len(loss) else None,
        },
        "session_features": feat.to_dict(orient="records"),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {OUT_JSON}")
    print("\nPosthoc (subset of baseline trades):")
    for r in posthoc:
        print(f"  {r['tag']:28} n={r['n']:2} WR={r['win_rate']:4.0f}% net={r['net_pnl_usdt']:+8.0f} big5R={r['big5r']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
