#!/usr/bin/env python3
"""King Keltner — vnpy CTA + 官方 BinanceLinearGateway 常驻实盘。"""

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
logger = logging.getLogger("kk_vnpy_runner")

from orb.kk.vnpy.runner import run_vnpy_kk  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="King Keltner vnpy 链路（官方 BinanceLinearGateway 直连）")
    ap.add_argument("--run-seconds", type=float, default=None, help="运行秒数（默认常驻）")
    ap.add_argument("--init-wait", type=float, default=30.0, help="策略 init/load_bar 等待秒数")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()

    try:
        out = run_vnpy_kk(run_seconds=args.run_seconds, init_wait_sec=args.init_wait)
    except ImportError as exc:
        logger.error(
            "vnpy 依赖未安装。请执行: pip install -r requirements-vnpy.txt （需含 vnpy_ctastrategy）"
        )
        logger.error("%s", exc)
        return 2

    if args.pretty:
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    else:
        logger.info("[kk-vnpy] result %s", json.dumps(out, ensure_ascii=False, default=str))
    if out.get("skipped"):
        return 0
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
