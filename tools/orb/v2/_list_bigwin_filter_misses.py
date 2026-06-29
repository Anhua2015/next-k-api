#!/usr/bin/env python3
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
trades = pd.read_csv(ROOT / "output/orb/v2/eval/coin_or10_3pct_tw0.trades.csv")
trades["pnl_r"] = trades["pnl_usdt"] / (trades["wallet_before"] * 0.03)
big5 = trades[trades["pnl_r"] >= 5].sort_values("pnl_usdt", ascending=False)
eod_wins = trades[(trades["outcome"] == "session_close") & (trades["pnl_usdt"] > 0)].sort_values("pnl_usdt", ascending=False)

print("=== >=5R big win days (%d) ===" % len(big5))
for _, r in big5.iterrows():
    print("  %s %5s +%.0fU (%.1fR)" % (r["session_date"], r["side"], r["pnl_usdt"], r["pnl_r"]))

print("\n=== All EOD win days (%d) ===" % len(eod_wins))
for _, r in eod_wins.iterrows():
    tag = ">=5R" if r["pnl_r"] >= 5 else "%.1fR" % r["pnl_r"]
    print("  %s %5s +%.0fU (%s)" % (r["session_date"], r["side"], r["pnl_usdt"], tag))

big_dates = set(big5["session_date"].astype(str))
bf = json.loads((ROOT / "output/orb/v2/eval/coin_or10_breakout_filters.json").read_text(encoding="utf-8"))
vf = json.loads((ROOT / "output/orb/v2/eval/coin_or10_binance1d_vol_filter.json").read_text(encoding="utf-8"))

variants = [(v["tag"], v.get("label", ""), set(v.get("trade_dates") or [])) for v in bf["variants"]]
variants += [(v["tag"], v.get("label", ""), set(v.get("trade_dates") or [])) for v in vf["variants"]]

print("\n=== Filter vs big win days kept ===")
seen = set()
for tag, label, dates in variants:
    if tag in seen or tag.startswith("posthoc") or tag.startswith("early_exit"):
        continue
    seen.add(tag)
    kept = len(big_dates & dates)
    missed = sorted(big_dates - dates)
    if tag == "baseline":
        print("  [baseline] %d/%d big wins  net +6066U  WR 18.1%%" % (kept, len(big_dates)))
        continue
    if kept >= len(big_dates):
        continue
    row = next(v for v in bf["variants"] + vf["variants"] if v["tag"] == tag)
    print(
        "  [%s] kept %d/%d  missed: %s  net %+.0fU  big5R %s  WR %s%%"
        % (tag, kept, len(big_dates), ", ".join(missed), row["net_pnl_usdt"], row.get("big_wins_5r"), row.get("win_rate"))
    )

feat = {r["session_date"]: r for r in bf["session_features"]}
med30 = bf["thresholds"]["median_first30m_range_pct"]
print("\n=== Big win day first30m range (why 30m filter skips) ===")
for d in sorted(big_dates):
    f = feat.get(d, {})
    r30 = f.get("first30m_range_pct")
    status = "PASS" if r30 and r30 >= med30 else "FAIL"
    print("  %s  first30m=%s%%  %s  (median=%.2f%%)" % (d, r30, status, med30))
