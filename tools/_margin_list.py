import json
import urllib.request

url = "https://next-k-api-production.up.railway.app/api/orb/signals?limit=500&status=settled"
with urllib.request.urlopen(url, timeout=30) as resp:
    rows = json.loads(resp.read().decode())["signals"]
rows.sort(key=lambda r: r.get("recorded_at_utc") or "")

# Default live leverage if not in row (ORB typically 5x from protocol logs)
LEV = 5.0

oc = {"win": "盈利", "loss": "止损", "session_close": "收盘平仓"}
print(f"{'#':>2} {'R':>2} {'Sym':<5} {'方向':<5} {'名义U':>8} {'杠杆':>4} {'保证金U':>8} {'盈亏U':>8}")
total_margin = 0.0
for i, r in enumerate(rows, 1):
    sym = str(r.get("symbol", "")).replace("USDT", "")
    notional = float(r.get("virtual_notional_usdt") or 0)
    margin = round(notional / LEV, 2) if notional and LEV else 0
    total_margin += margin
    pnl = float(r.get("pnl_usdt") or 0)
    st = oc.get(str(r.get("outcome", "")), "")
    print(
        f"{i:2d} R{r.get('robot_id')}  {sym:<5} {r.get('side'):<5} "
        f"{notional:8.2f} {LEV:4.0f}x {margin:8.2f} {pnl:+8.4f}  {st}"
    )
print(f"\n共 {len(rows)} 笔，保证金合计约 {total_margin:.2f} U（按 {LEV:.0f}x 估算）")
