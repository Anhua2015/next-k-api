#!/usr/bin/env python3
"""ORB 纸面扫描 CLI。"""

from __future__ import annotations

import argparse
import json
import sys

from orb.paper import run_resolve_only, run_scan


def main() -> int:
    ap = argparse.ArgumentParser(description="ORB 量价策略 — 纸面扫描")
    ap.add_argument("--resolve-only", action="store_true")
    ap.add_argument("--no-resolve", action="store_true")
    args = ap.parse_args()
    if args.resolve_only:
        out = run_resolve_only()
    else:
        out = run_scan(do_resolve=not args.no_resolve)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0 if out.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
