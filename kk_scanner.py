#!/usr/bin/env python3
"""King Keltner 纸面扫描 CLI（RTH + EOD，与 ORB 隔离）。"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from env_loader import load_env_oi

load_env_oi()


def _configure_logging() -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


_configure_logging()
logger = logging.getLogger("kk_scanner")

from orb.kk.paper import run_scan_kk  # noqa: E402


def _scan_summary(out: dict) -> dict:
    summary = out.get("summary") or {}
    return {
        "ok": out.get("ok"),
        "lane": out.get("lane"),
        "skipped": out.get("skipped"),
        "reason": out.get("reason"),
        "opens": summary.get("opens", len(out.get("opens") or [])),
        "closes": summary.get("closes", len(out.get("closes") or [])),
        "eod_closes": summary.get("eod_closes", 0),
        "shadow": out.get("shadow"),
        "symbols": len(out.get("symbols") or []),
        "session_date": out.get("session_date"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="King Keltner — RTH+EOD 纸面扫描")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()
    out = run_scan_kk()
    if args.pretty:
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    else:
        logger.info("[kk] scan result %s", json.dumps(_scan_summary(out), ensure_ascii=False, default=str))
    if not out.get("ok", True):
        return 1
    if out.get("skipped"):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
