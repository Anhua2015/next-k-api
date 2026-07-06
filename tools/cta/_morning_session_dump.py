import pandas as pd

df = pd.read_csv("output/kk/backtest_2026-07-02_full.csv")
df = df[df["event"] == "close"].copy()
df["et"] = pd.to_datetime(df["time_et"])
lo = pd.Timestamp("2026-07-02 09:30:00")
hi = pd.Timestamp("2026-07-02 12:00:00")
m = df[(df["et"] >= lo) & (df["et"] <= hi)].sort_values("et")
print("=== morning 09:30-12:00 ET (backtest 14U) ===")
print(f"closes={len(m)} net={m.pnl_usdt.sum():+.4f} wins={(m.pnl_usdt > 0).sum()}/{len(m)}")
print()
for _, r in m.iterrows():
    bj = str(r["time_cn"]).split(" ")[1]
    et = str(r["time_et"]).split(" ")[1]
    print(
        f"{et}  北京{bj}  {r['symbol']:4s} {r['side']:5s} {str(r['outcome']):4s}  "
        f"pnl={r['pnl_usdt']:+.4f}U  notion={r['notional_usdt']:.1f}"
    )
print("\nby symbol:")
print(m.groupby("symbol")["pnl_usdt"].agg(["count", "sum"]).round(4))
