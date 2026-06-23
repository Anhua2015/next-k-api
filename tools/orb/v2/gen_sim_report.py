#!/usr/bin/env python3
"""Generate full markdown report from sim_live_session outputs."""
import csv
import json
import sys
from pathlib import Path


def main() -> None:
    tag = sys.argv[1] if len(sys.argv) > 1 else "2026-06-01_2026-06-23"
    root = Path("output/orb/v2/eval")
    trades = list(csv.DictReader((root / f"live_sim_{tag}.trades.csv").open(encoding="utf-8")))
    daily = list(csv.DictReader((root / f"live_sim_{tag}.daily.csv").open(encoding="utf-8")))
    meta = json.loads((root / f"live_sim_{tag}.json").read_text(encoding="utf-8"))
    out = root / f"live_sim_{tag}.report.md"

    lines: list[str] = []
    lines.append("# ORB 实盘逻辑回测 — 2026年6月完整明细")
    lines.append("")
    lines.append("## 回测口径")
    lines.append("- 逻辑: orb/v2/paper.py + df5_for_breakout_score + Gate BS>=45")
    re = meta.get("robot_equity_usdt", 14)
    lines.append(f"- 资金: 8 robot x {re}U，跨日复利，robot_reuse")
    fm = meta.get("fee_model") or {}
    bps = fm.get("bps_per_side", 4)
    lines.append(f"- 费用: notional × {bps}bps/边 × 2（开平往返）")
    lines.append("- 数据: 本地 K 线缓存 33 标")
    dr = meta["date_range"]
    lines.append(f"- 区间: {dr['from']} ~ {dr['to']} ({dr['sessions']} sessions)")
    lines.append("")
    s = meta["summary"]
    lines.append("## 月度汇总")
    lines.append(f"- 总开单: {s['total_opens']} 笔")
    lines.append(f"- 毛 PnL: {s['total_gross_pnl_usdt']:+.2f} U")
    lines.append(f"- 手续费: -{s['total_fees_usdt']:.2f} U")
    lines.append(f"- 净 PnL: {s['total_net_pnl_usdt']:+.2f} U")
    lines.append(f"- BS 拒单累计: {s['total_bs_skips']} 次")
    lines.append("")
    lines.append("## 每日汇总（完整）")
    lines.append("")
    lines.append("| 日期 | 宏观过滤 | 开单 | Gate拒 | BS拒 | 毛PnL | 费 | 净PnL |")
    lines.append("|------|----------|------|--------|------|-------|-----|-------|")
    for d in daily:
        lines.append(
            "| {session_date} | {macro_skip_day} | {opens} | {gate_skips} | {bs_skips} | "
            "{gross_pnl_usdt} | {fees_usdt} | {net_pnl_usdt} |".format(**d)
        )
    lines.append("")
    lines.append(f"## 逐笔明细（完整 {len(trades)} 笔）")
    lines.append("")
    lines.append("| # | 日期 | ET | 标的 | 方向 | 入场 | P | BS | R | 毛PnL | 费 | 净PnL | 结果 |")
    lines.append("|---|------|-----|------|------|------|-----|-----|---|-------|-----|-------|------|")
    for i, t in enumerate(trades, 1):
        sym = (t.get("symbol") or "").replace("USDT", "")
        row = {k: t.get(k, "") for k in t}
        row["i"] = i
        row["sym"] = sym
        lines.append(
            "| {i} | {session_date} | {scan_et} | {sym} | {side} | {entry} | {p_true} | "
            "{breakout_score} | R{robot_id} | {pnl_usdt_gross} | {fee_usdt} | {pnl_usdt} | {outcome} |".format(
                **row
            )
        )

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"trades={len(trades)} report={out}")


if __name__ == "__main__":
    main()
