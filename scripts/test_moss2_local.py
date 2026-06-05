#!/usr/bin/env python3
"""本地快速验证：QuantStats / suggest 单币 / sync 汇总 API。"""

from __future__ import annotations

import os
import sys

# scripts/ → next-k-api 根
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def main() -> int:
    os.environ.setdefault("NEXT_K_MAINTENANCE_TOKEN", "")
    print("=== 1) format_provision_summary ===")
    from moss2.auto_provision import format_provision_summary, run_lane_auto_provision
    from moss2.db import migrate_moss2_tables
    import sqlite3
    from unittest.mock import patch

    conn = sqlite3.connect(":memory:")
    migrate_moss2_tables(conn.cursor())
    conn.commit()
    with (
        patch("moss2.auto_provision.cfg.MOSS2_SEED_BASES", ("BTC",)),
        patch(
            "moss2.auto_provision.provision_symbol",
            return_value={
                "action": "create",
                "symbol": "BTCUSDT",
                "recommended_template": "balanced",
                "suggest_reason": "backtest_selection_pass",
                "auto_enabled": True,
                "enable_reason": "suggest_selection_pass",
                "evolve": {
                    "candidate": {
                        "summary": {
                            "sharpe": 0.9,
                            "total_return": 0.03,
                            "max_drawdown": -0.15,
                            "total_trades": 28,
                        },
                        "discipline": {},
                    }
                },
            },
        ),
    ):
        stats = run_lane_auto_provision(conn)
    stats["summary_text"] = format_provision_summary(stats)
    print(stats["summary_text"])
    print()

    print("=== 2) quantstats ===")
    from moss2.reports.quantstats_report import quantstats_available

    print("quantstats_installed:", quantstats_available())
    print()

    print("=== 3) FastAPI sync auto-provision (mock) ===")
    from fastapi.testclient import TestClient
    from main import app

    with patch(
        "moss2.auto_provision.run_lane_auto_provision",
        return_value={
            "ok": True,
            "created": 1,
            "updated": 0,
            "maintained": 0,
            "skipped": 0,
            "enabled_profiles": 1,
            "sync_enabled_approved": 0,
            "results": [],
        },
    ):
        client = TestClient(app)
        r = client.post("/api/moss2/maintenance/auto-provision?sync=true")
    print("status:", r.status_code)
    body = r.json()
    print("sync:", body.get("sync"))
    print("summary_text:", body.get("summary_text"))
    print()

    print("=== 4) reports/status ===")
    r2 = client.get("/api/moss2/reports/status")
    print("status:", r2.status_code, r2.json())
    print()

    do_suggest = "--suggest" in sys.argv
    if do_suggest:
        print("=== 5) suggest_profile BTCUSDT (live, slow) ===")
        from moss2.onboarding import suggest_profile

        out = suggest_profile("BTC", backtest_bars=1500)
        print("ok:", out.get("ok"), "reason:", out.get("reason"))
        print("template:", out.get("recommended_template"))
        if out.get("template_scores"):
            for row in out["template_scores"][:4]:
                print(
                    " ",
                    row.get("template"),
                    "pass=",
                    row.get("passes_discipline"),
                    "sharpe=",
                    row.get("sharpe"),
                )
    else:
        print("(跳过 live suggest；加 --suggest 可测单币四模板回测)")
    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
