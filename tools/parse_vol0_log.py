import re
from collections import Counter

path = r"c:\Users\18006\Downloads\logs.1783028268195.log"
pat = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*OrderRequest\(symbol='(\w+)'.*"
    r"direction=<Direction\.(\w+).*volume=([\d.]+).*offset=<Offset\.(\w+).*reference='([^']+)'"
)
zero, ok = [], []
with open(path, encoding="utf-8", errors="replace") as f:
    for line in f:
        m = pat.search(line)
        if not m:
            continue
        ts, sym, direc, vol, off, ref = m.groups()
        rec = dict(ts=ts, sym=sym.replace("_SWAP_BINANCE", ""), dir=direc, vol=float(vol), off=off, ref=ref)
        (zero if float(vol) <= 0 else ok).append(rec)

print("volume=0 orders:", len(zero))
print("volume>0 orders:", len(ok))
if zero:
    print("first:", zero[0])
    print("last:", zero[-1])
print("\nzero by symbol:")
for k, v in Counter(r["sym"] for r in zero).most_common():
    print(f"  {k}: {v}")
print("\nzero dir+offset:")
for k, v in Counter((r["dir"], r["off"]) for r in zero).most_common():
    print(f"  {k[0]} {k[1]}: {v}")
if ok:
    print("\n--- volume>0 sample (first 20) ---")
    for r in ok[:20]:
        print(f"  {r['ts']} {r['sym']:14s} {r['dir']:5s} vol={r['vol']} {r['off']}")
    print("\nvolume>0 by symbol:")
    for k, v in Counter(r["sym"] for r in ok).most_common():
        print(f"  {k}: {v}")
