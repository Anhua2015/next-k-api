#!/usr/bin/env python3
"""生成本地可打开的 Moss2 汇总 + 报告预览 HTML（无 quantstats 也能看）。"""

from __future__ import annotations

import json
import os
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

OUT_DIR = _ROOT / "data" / "moss2_reports"
OUT_HTML = OUT_DIR / "local_preview.html"
OUT_JSON = OUT_DIR / "local_preview_data.json"


def _demo_stats() -> dict:
    import sqlite3
    from moss2.auto_provision import format_provision_summary, run_lane_auto_provision
    from moss2.db import migrate_moss2_tables

    def row(sym, action, tpl, sr, en, reason, sh=None, ret=None, mdd=None, tr=None):
        r = {
            "symbol": sym,
            "action": action,
            "recommended_template": tpl,
            "suggest_reason": sr,
            "auto_enabled": en,
            "enable_reason": reason,
        }
        if sh is not None:
            r["evolve"] = {
                "candidate": {
                    "summary": {
                        "sharpe": sh,
                        "total_return": ret,
                        "max_drawdown": mdd,
                        "total_trades": tr,
                    },
                    "discipline": {"ev": {"ev_per_trade_pct": 0.012}},
                }
            }
        return r

    conn = sqlite3.connect(":memory:")
    migrate_moss2_tables(conn.cursor())
    conn.commit()
    fake = [
        row("BTCUSDT", "create", "balanced", "backtest_selection_pass", True, "suggest_selection_pass", 1.12, 0.048, -0.11, 42),
        row("ETHUSDT", "update", "momentum", "backtest_selection_pass", True, "suggest_selection_pass", 0.85, 0.031, -0.18, 38),
        row("SOLUSDT", "create", "trend", "regime_hint_only", False, "gates_fail_or_no_candidate"),
        row("BNBUSDT", "skip", "balanced", "klines_unavailable", False, "suggest_failed"),
        row("ARBUSDT", "maintain", "balanced", "backtest_selection_pass", False, "already_enabled", 0.72, 0.02, -0.14, 31),
    ]
    with (
        patch("moss2.auto_provision.cfg.MOSS2_SEED_BASES", tuple("x" for _ in fake)),
        patch("moss2.auto_provision.provision_symbol", side_effect=fake),
        patch("moss2.auto_provision.sync_enable_approved_profiles", return_value=1),
    ):
        stats = run_lane_auto_provision(conn)
    stats["summary_text"] = format_provision_summary(stats)
    stats["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    return stats


def _synthetic_equity(n: int = 672, seed: int = 42) -> list[float]:
    """默认 672 根 15m ≈ 7 天，日收益样本足够 QuantStats。"""
    import random

    rng = random.Random(seed)
    eq = 10000.0
    out = [eq]
    for _ in range(n - 1):
        eq *= 1 + rng.uniform(-0.008, 0.012)
        out.append(round(eq, 2))
    return out


def _try_quantstats_html(equity: list[float]) -> dict | None:
    try:
        from moss2.reports.quantstats_report import (
            equity_list_to_daily_returns,
            generate_moss2_tearsheet,
            quantstats_available,
        )

        if not quantstats_available():
            return None
        ret = equity_list_to_daily_returns(equity, bar_minutes=15)
        return generate_moss2_tearsheet(
            returns=ret,
            title="Moss2 local demo · BTCUSDT backtest",
            filename_stem="local_qs_demo",
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _build_html(stats: dict, equity: list[float], qs: dict | None) -> str:
    qs_block = ""
    if qs and qs.get("ok") and qs.get("filename"):
        qs_block = (
            f'<p class="ok">QuantStats 已生成：<a href="{qs["filename"]}" target="_blank" rel="noopener">'
            f'{qs["filename"]}</a>'
            f' · Sharpe {qs.get("stats", {}).get("sharpe", "—")}'
            f' · MDD {qs.get("stats", {}).get("max_drawdown", "—")}'
            f' · n={qs.get("observations", "")}</p>'
        )
    elif qs:
        qs_block = f'<p class="warn">QuantStats：{qs.get("error") or qs.get("hint") or "未安装"}</p>'
    else:
        qs_block = '<p class="warn">本机未安装 quantstats，下方为内置预览曲线（非官方 tearsheet）。</p>'

    pts = equity
    w, h = 640, 200
    mn, mx = min(pts), max(pts)
    span = mx - mn or 1
    coords = []
    for i, v in enumerate(pts):
        x = 40 + (w - 60) * i / max(len(pts) - 1, 1)
        y = 20 + (h - 40) * (1 - (v - mn) / span)
        coords.append(f"{x:.1f},{y:.1f}")
    poly = " ".join(coords)

    summary_pre = stats.get("summary_text", "").replace("&", "&amp;").replace("<", "&lt;")
    head_json = {k: stats[k] for k in stats if k != "results"}
    results_json = json.dumps(stats.get("results") or [], ensure_ascii=False, indent=2)
    head_json_s = json.dumps(head_json, ensure_ascii=False, indent=2)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <title>Moss2 本地预览</title>
  <style>
    body {{ font-family: ui-sans-serif, system-ui, sans-serif; background:#0d1117; color:#e6edf3; margin:24px; line-height:1.5; }}
    h1 {{ color:#3fb950; font-size:1.25rem; }}
    h2 {{ color:#58a6ff; font-size:1rem; margin-top:1.5rem; }}
    pre {{ background:#161b22; border:1px solid #30363d; padding:12px; border-radius:8px; overflow:auto; font-size:12px; }}
    .ok {{ color:#3fb950; }}
    .warn {{ color:#d29922; }}
    svg {{ background:#161b22; border-radius:8px; border:1px solid #30363d; }}
    a {{ color:#58a6ff; }}
    table {{ border-collapse:collapse; font-size:12px; width:100%; }}
    th, td {{ border:1px solid #30363d; padding:6px 8px; text-align:left; }}
    th {{ background:#21262d; }}
  </style>
</head>
<body>
  <h1>Moss2 本地预览 · {stats.get("generated_at_utc", "")[:19]} UTC</h1>
  <p>对应维护面板 <code>sync=true</code> 全自动后的 <strong>summary_text</strong> + 回测 equity 示意。</p>

  <h2>1. 全自动汇总（summary_text）</h2>
  <pre>{summary_pre}</pre>

  <h2>2. 统计头 JSON</h2>
  <pre>{head_json_s}</pre>

  <h2>3. 回测 equity 曲线</h2>
  {qs_block}
  <svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">
    <polyline fill="none" stroke="#3fb950" stroke-width="2" points="{poly}"/>
  </svg>
  <p style="font-size:11px;color:#8b949e">演示数据 {len(pts)} 点 · 初始 {pts[0]:.0f} → 末 {pts[-1]:.0f} USDT</p>

  <h2>4. 每币 results（JSON）</h2>
  <pre>{results_json}</pre>
</body>
</html>"""


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stats = _demo_stats()
    equity = _synthetic_equity()
    qs = _try_quantstats_html(equity)

    OUT_JSON.write_text(
        json.dumps({"stats": stats, "quantstats": qs, "equity_len": len(equity)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    OUT_HTML.write_text(_build_html(stats, equity, qs), encoding="utf-8")

    print("written:", OUT_HTML)
    print("json:", OUT_JSON)
    print()
    print(stats["summary_text"])
    if qs and qs.get("path"):
        print("quantstats html:", qs["path"])

    uri = OUT_HTML.as_uri()
    try:
        webbrowser.open(uri)
        print("opened browser:", uri)
    except Exception as e:
        print("open manually:", uri, e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
