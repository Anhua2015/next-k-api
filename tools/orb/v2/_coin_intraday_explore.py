#!/usr/bin/env python3
"""COIN open-to-close intraday exploration: EOD winners vs SL losers."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi  # noqa: E402

load_env_oi()

from orb.core.config import OrbConfig  # noqa: E402
from orb.core.kline_cache import load_klines  # noqa: E402
from orb.core.session import (
    compute_opening_range,
    session_anchor_ms,
    session_close_ms,
    session_slice,
)  # noqa: E402
from tools.orb.v2.backtest_universe import universe_session_dates  # noqa: E402


def main() -> int:
    cfg = OrbConfig.from_env()
    cfg.or_minutes = 15
    cfg.sl_mode = "atr_pct"
    cfg.exit_mode = "eod"
    cfg.macro_filter = False

    sym = "COINUSDT"
    df5 = load_klines(sym, "5m")
    df1d = load_klines(sym, "1d")
    dates = [d for d in universe_session_dates([sym], cfg) if "2026-02-09" <= d <= "2026-06-24"]

    samples_csv = ROOT / "output/orb_fake_breakout_samples_coin.csv"
    trades = pd.read_csv(samples_csv)
    trades["win"] = trades["outcome"] == "session_close"

    print("=" * 70)
    print(f"COIN ORB analysis | {len(dates)} sessions | {len(trades)} trades")
    print("=" * 70)

    sess_rows = []
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
        hi = float(sess["high"].max())
        lo = float(sess["low"].min())
        rng_pct = (hi - lo) / open_p * 100
        ret_pct = (close_p - open_p) / open_p * 100

        pack = compute_opening_range(
            sess,
            or_minutes=15,
            bar_step_ms=300_000,
            asof_open_ms=close_ms - 60_000,
            tz=cfg.session_tz,
            session_open_time=cfg.session_open_time,
        )
        or_w = float(pack["or_width_pct"]) if pack else np.nan
        or_h, or_l = (float(pack["or_high"]), float(pack["or_low"])) if pack else (np.nan, np.nan)

        prior = df1d[df1d["open_time"] < anchor].tail(1)
        gap_pct = np.nan
        if len(prior):
            prev_c = float(prior.iloc[-1]["close"])
            gap_pct = (open_p - prev_c) / prev_c * 100

        or_mid = (or_h + or_l) / 2 if pack else np.nan
        or_bias = (close_p - or_mid) / or_mid * 100 if pack else np.nan

        morning = sess.iloc[:24] if len(sess) >= 24 else sess
        aft = sess.iloc[24:] if len(sess) > 24 else pd.DataFrame()
        morn_ret = (float(morning.iloc[-1]["close"]) - open_p) / open_p * 100
        aft_ret = (
            (close_p - float(morning.iloc[-1]["close"])) / float(morning.iloc[-1]["close"]) * 100
            if len(aft)
            else 0.0
        )

        vol = sess["volume"].astype(float)
        or_bars = 3
        or_vol = vol.iloc[:or_bars].sum()
        rest_vol = vol.iloc[or_bars:].sum()
        vol_ratio = or_vol / rest_vol * (len(vol) - or_bars) / or_bars if rest_vol > 0 else np.nan

        sess_rows.append(
            {
                "date": d,
                "open": open_p,
                "close": close_p,
                "ret_pct": ret_pct,
                "range_pct": rng_pct,
                "or_width_pct": or_w,
                "gap_pct": gap_pct,
                "or_bias_pct": or_bias,
                "morn_ret": morn_ret,
                "aft_ret": aft_ret,
                "vol_or_ratio": vol_ratio,
            }
        )

    sess_df = pd.DataFrame(sess_rows)
    print("\n[1] Session-level stats")
    print(f"  avg range: {sess_df['range_pct'].mean():.2f}% (med {sess_df['range_pct'].median():.2f}%)")
    print(f"  avg ret:   {sess_df['ret_pct'].mean():+.2f}% (std {sess_df['ret_pct'].std():.2f}%)")
    print(f"  OR width:  {sess_df['or_width_pct'].mean():.2f}% (med {sess_df['or_width_pct'].median():.2f}%)")
    print(f"  gap avg:   {sess_df['gap_pct'].mean():+.2f}%")
    print(f"  morning:   {sess_df['morn_ret'].mean():+.2f}% | afternoon: {sess_df['aft_ret'].mean():+.2f}%")

    up = (sess_df["ret_pct"] > 0.5).sum()
    dn = (sess_df["ret_pct"] < -0.5).sum()
    chop = len(sess_df) - up - dn
    print(f"  trend days: up>{0.5}%={up} down<-0.5%={dn} chop={chop}")

    wins = trades[trades["win"]]
    losses = trades[~trades["win"]]
    print("\n[2] Trade outcomes")
    print(
        f"  total={len(trades)} EOD={len(wins)} ({len(wins)/len(trades)*100:.0f}%) "
        f"SL={len(losses)} ({len(losses)/len(trades)*100:.0f}%)"
    )
    print(f"  EOD PnL: {wins['pnl_usdt'].sum():+.0f}U | SL PnL: {losses['pnl_usdt'].sum():+.0f}U")
    print(f"  EOD avg R: {wins['pnl_r'].mean():+.1f}R | big wins (>=5R): {(wins['pnl_r'] >= 5).sum()}")

    feats = [
        "f_or_width_pct",
        "f_vwap_dist_pct",
        "f_risk_frac_pct",
        "f_minutes_after_or",
        "f_gap_pct",
        "f_atr_pct",
        "fake_breakout_p",
    ]
    print("\n[3] EOD vs SL feature comparison")
    print(f"{'feature':<22} {'EOD':>10} {'SL':>10} {'delta':>10}")
    for f in feats:
        if f not in trades.columns:
            continue
        wv = wins[f].astype(float).mean()
        lv = losses[f].astype(float).mean()
        print(f"{f:<22} {wv:>10.2f} {lv:>10.2f} {wv - lv:>+10.2f}")

    print(
        f"\n  fake_breakout=0: EOD {(wins['fake_breakout'] == 0).sum()}/{len(wins)} | "
        f"SL fake=1: {(losses['fake_breakout'] == 1).sum()}/{len(losses)}"
    )

    for side in ["LONG", "SHORT"]:
        sub = trades[trades["side"].str.contains("LONG" if side == "LONG" else "SHORT")]
        w = sub[sub["win"]]
        print(
            f"\n  {side}: n={len(sub)} EOD={len(w)/len(sub)*100:.0f}% "
            f"EOD PnL={w['pnl_usdt'].sum():+.0f}U SL PnL={sub[~sub['win']]['pnl_usdt'].sum():+.0f}U"
        )

    print("\n[4] Entry timing (minutes after OR)")
    for label, sub in [("EOD", wins), ("SL", losses)]:
        m = sub["f_minutes_after_or"].astype(float)
        print(
            f"  {label}: avg={m.mean():.0f}m | <=15m={(m <= 15).sum()} "
            f"15-60m={((m > 15) & (m <= 60)).sum()} >60m={(m > 60).sum()}"
        )

    print("\n[5] OR width buckets")
    trades["or_bucket"] = pd.cut(
        trades["f_or_width_pct"],
        bins=[0, 1.5, 2.5, 3.5, 5, 100],
        labels=["<1.5%", "1.5-2.5%", "2.5-3.5%", "3.5-5%", ">5%"],
    )
    for b, g in trades.groupby("or_bucket", observed=True):
        wr = g["win"].mean()
        pnl = g["pnl_usdt"].sum()
        avg_r = g.loc[g["win"], "pnl_r"].mean() if g["win"].any() else 0.0
        print(f"  {b}: n={len(g):2d} WR={wr*100:.0f}% PnL={pnl:+.0f}U avgR={avg_r:.1f}")

    print("\n[6] VWAP distance at entry")
    trades["vwap_side"] = np.where(
        trades["f_vwap_dist_pct"] > 0.5,
        "above",
        np.where(trades["f_vwap_dist_pct"] < -0.5, "below", "near"),
    )
    for v, g in trades.groupby("vwap_side"):
        print(f"  {v}: n={len(g)} WR={g['win'].mean()*100:.0f}% PnL={g['pnl_usdt'].sum():+.0f}U")

    print("\n[7] Gap buckets")
    trades["gap_b"] = pd.cut(
        trades["f_gap_pct"].fillna(0),
        bins=[-100, -2, -0.5, 0.5, 2, 100],
        labels=["gap<-2%", "-2~-0.5%", "flat", "+0.5~2%", "gap>2%"],
    )
    for b, g in trades.groupby("gap_b", observed=True):
        print(f"  {b}: n={len(g)} WR={g['win'].mean()*100:.0f}% PnL={g['pnl_usdt'].sum():+.0f}U")

    merged = trades.merge(sess_df, left_on="session_date", right_on="date", how="left")
    merged["aligned"] = ((merged["side"].str.contains("LONG")) & (merged["ret_pct"] > 0)) | (
        (merged["side"].str.contains("SHORT")) & (merged["ret_pct"] < 0)
    )
    print("\n[8] Trade direction vs day trend")
    for label, sub in [("aligned", merged[merged["aligned"]]), ("counter", merged[~merged["aligned"]])]:
        print(f"  {label}: n={len(sub)} WR={sub['win'].mean()*100:.0f}% PnL={sub['pnl_usdt'].sum():+.0f}U")

    big = wins[wins["pnl_r"] >= 5]
    print(f"\n[9] Big winners (>=5R, n={len(big)})")
    print(f"  LONG={(big['side'].str.contains('LONG')).sum()} SHORT={(big['side'].str.contains('SHORT')).sum()}")
    print(f"  or_width avg={big['f_or_width_pct'].mean():.2f}% afterOR={big['f_minutes_after_or'].mean():.0f}m")
    print(f"  fake_p avg={big['fake_breakout_p'].mean():.3f} vwap_dist={big['f_vwap_dist_pct'].mean():+.2f}%")
    for _, r in big.sort_values("pnl_r", ascending=False).head(8).iterrows():
        print(
            f"    {r['session_date']} {r['side'][:1]} +{r['pnl_r']:.1f}R ({r['pnl_usdt']:+.0f}U) "
            f"OR={r['f_or_width_pct']:.1f}% gap={r['f_gap_pct']:.1f}% after={r['f_minutes_after_or']:.0f}m"
        )

    print("\n[10] Intraday path on EOD vs SL trade days")
    for outcome_label, mask in [("EOD", trades["win"]), ("SL", ~trades["win"])]:
        sub_dates = trades.loc[mask, "session_date"].tolist()
        sub_sess = sess_df[sess_df["date"].isin(sub_dates)]
        print(
            f"  {outcome_label} ({len(sub_sess)}d): ret={sub_sess['ret_pct'].mean():+.2f}% "
            f"range={sub_sess['range_pct'].mean():.2f}% or={sub_sess['or_width_pct'].mean():.2f}% "
            f"morn={sub_sess['morn_ret'].mean():+.2f}% aft={sub_sess['aft_ret'].mean():+.2f}%"
        )

    slip_json = ROOT / "output/orb/v2/eval/coin_or15_chase_slip_analysis_2026-02-09_2026-06-24.json"
    if slip_json.exists():
        s = json.loads(slip_json.read_text(encoding="utf-8"))["summary"]
        print("\n[11] Chase slip impact")
        print(f"  ideal gross={s['net_gross_ideal_u']:+.0f}U chase={s['net_gross_chase_u']:+.0f}U cost={s['net_gross_chase_cost_u']:+.0f}U")
        print(f"  EOD missed profit={s['eod_trades_missed_profit_from_chase_u']:+.0f}U on {s['eod_trades_missed_profit_count']} trades")

    print(f"\n[12] Filter rules (baseline PnL={trades['pnl_usdt'].sum():+.0f}U)")
    rules = [
        ("fake_p < 0.75", trades["fake_breakout_p"] < 0.75),
        ("fake_p < 0.70", trades["fake_breakout_p"] < 0.70),
        ("or_width >= 2.0%", trades["f_or_width_pct"] >= 2.0),
        ("or_width >= 2.5%", trades["f_or_width_pct"] >= 2.5),
        ("or_width 2-4%", (trades["f_or_width_pct"] >= 2) & (trades["f_or_width_pct"] <= 4)),
        ("entry <=30m after OR", trades["f_minutes_after_or"] <= 30),
        ("entry <=60m after OR", trades["f_minutes_after_or"] <= 60),
        ("|vwap_dist| >= 0.8%", trades["f_vwap_dist_pct"].abs() >= 0.8),
        ("combo fake_p<0.75 & or>=2%", (trades["fake_breakout_p"] < 0.75) & (trades["f_or_width_pct"] >= 2)),
        ("combo fake_p<0.75 & entry<=30m", (trades["fake_breakout_p"] < 0.75) & (trades["f_minutes_after_or"] <= 30)),
    ]
    for name, mask in rules:
        sub = trades[mask]
        if len(sub) == 0:
            continue
        wr = sub["win"].mean()
        pnl = sub["pnl_usdt"].sum()
        print(f"  {name:<35} keep={len(sub):2d} WR={wr*100:.0f}% PnL={pnl:+.0f}U avg={pnl/len(sub):+.1f}U/tr")

    print("\n" + "=" * 70)

    # Post-entry path analysis
    print("\n[13] Post-entry price path (entry -> close)")
    path_rows = []
    for _, t in trades.iterrows():
        d = t["session_date"]
        side = t["side"]
        entry = float(t["entry"])
        sl = float(t["sl"])
        ts = pd.Timestamp(d + " 12:00:00", tz=cfg.session_tz)
        anchor = session_anchor_ms(
            int(ts.value // 1_000_000),
            tz=cfg.session_tz,
            session_open_time=cfg.session_open_time,
        )
        close_ms = session_close_ms(anchor, tz=cfg.session_tz, session_close_time=cfg.session_close_time)
        entry_ms = anchor + 15 * 60_000 + int(float(t["f_minutes_after_or"])) * 60_000
        path = df5[(df5["open_time"] >= entry_ms) & (df5["open_time"] <= close_ms)]
        if path.empty:
            continue
        hi = path["high"].astype(float)
        lo = path["low"].astype(float)
        cl = path["close"].astype(float)
        if "LONG" in side:
            mfe = (hi.max() - entry) / entry * 100
            mae = (entry - lo.min()) / entry * 100
            eod_ret = (float(cl.iloc[-1]) - entry) / entry * 100
        else:
            mfe = (entry - lo.min()) / entry * 100
            mae = (hi.max() - entry) / entry * 100
            eod_ret = (entry - float(cl.iloc[-1])) / entry * 100
        path_rows.append({"win": t["win"], "mfe": mfe, "mae": mae, "eod_ret": eod_ret, "pnl_r": t["pnl_r"]})

    pdf = pd.DataFrame(path_rows)
    for label, sub in [("EOD", pdf[pdf["win"]]), ("SL", pdf[~pdf["win"]])]:
        print(
            f"  {label} n={len(sub)} MFE={sub['mfe'].mean():.2f}% MAE={sub['mae'].mean():.2f}% "
            f"EOD={sub['eod_ret'].mean():+.2f}%"
        )
    w = pdf[pdf["win"]]
    if len(w) and w["mfe"].mean() > 0:
        print(f"  EOD winners capture {w['eod_ret'].mean() / w['mfe'].mean() * 100:.0f}% of max favorable move")

    # 1h continuation
    print("\n[14] 1-hour post-entry continuation")
    h1_rows = []
    for _, t in trades.iterrows():
        d = t["session_date"]
        entry = float(t["entry"])
        side = t["side"]
        mins = float(t["f_minutes_after_or"])
        ts = pd.Timestamp(d + " 12:00:00", tz=cfg.session_tz)
        anchor = session_anchor_ms(
            int(ts.value // 1_000_000),
            tz=cfg.session_tz,
            session_open_time=cfg.session_open_time,
        )
        entry_ms = anchor + 15 * 60_000 + int(mins) * 60_000
        end1h = entry_ms + 60 * 60_000
        path = df5[(df5["open_time"] >= entry_ms) & (df5["open_time"] <= end1h)]
        if len(path) < 2:
            continue
        p1 = float(path.iloc[-1]["close"])
        h1_ret = (p1 - entry) / entry * 100 if "LONG" in side else (entry - p1) / entry * 100
        h1_rows.append({"win": t["win"], "h1_ret": h1_ret})
    h1 = pd.DataFrame(h1_rows)
    for label, sub in [("EOD", h1[h1["win"]]), ("SL", h1[~h1["win"]])]:
        pos = (sub["h1_ret"] > 0).mean() * 100
        print(f"  {label}: 1h ret={sub['h1_ret'].mean():+.2f}% still positive={pos:.0f}%")

    print("\n" + "=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
