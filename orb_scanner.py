#!/usr/bin/env python3
"""ORB 纸面扫描 CLI。"""

from __future__ import annotations

import argparse
import json
import logging
import sys


def _configure_logging() -> None:
    """与 main.py 一致走 stdout，避免 Railway 上 stdout/stderr 交错。"""
    root = logging.getLogger()
    if root.handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


_configure_logging()
logger = logging.getLogger("orb_scanner")

from orb.paper import run_resolve_only, run_scan  # noqa: E402


def _scan_summary(out: dict) -> dict:
    """调度日志用紧凑摘要（避免多行 JSON 污染）。"""
    summary: dict = {
        "ok": out.get("ok"),
        "skipped": out.get("skipped"),
        "written": out.get("written"),
        "reason": out.get("reason"),
        "opens": len(out.get("opens") or []),
        "symbols": len(out.get("symbols") or []),
    }
    mc = out.get("macro_calendar")
    if isinstance(mc, dict):
        summary["macro"] = {
            "total_dates": mc.get("total_dates"),
            "fomc_live": mc.get("fomc_live"),
            "cpi_live": mc.get("cpi_live"),
        }
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="ORB 量价策略 — 纸面扫描")
    ap.add_argument("--resolve-only", action="store_true")
    ap.add_argument("--no-resolve", action="store_true")
    ap.add_argument(
        "--pretty",
        action="store_true",
        help="打印完整多行 JSON（本地调试）；默认可读单行摘要",
    )
    args = ap.parse_args()
    if args.resolve_only:
        out = run_resolve_only()
    else:
        out = run_scan(do_resolve=not args.no_resolve)
    if args.pretty:
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    else:
        logger.info(
            "[orb] scan result %s",
            json.dumps(_scan_summary(out), ensure_ascii=False, default=str),
        )
    return 0 if out.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
