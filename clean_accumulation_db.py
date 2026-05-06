#!/usr/bin/env python3
"""
清理 accumulation.db 中的看盘表（与 API 维护接口逻辑一致）。

用法（在 next-k-api 目录或设置好 DATA_DIR）:
  python clean_accumulation_db.py                 # 默认只清空 ambush_watch
  python clean_accumulation_db.py --ambush
  python clean_accumulation_db.py --heat-accum
  python clean_accumulation_db.py --all

环境变量:
  DATA_DIR  — accumulation.db 所在目录，未设置则为本脚本所在目录。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 确保可导入同目录下的 accumulation_radar
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description="清空 accumulation.db 看盘表")
    parser.add_argument("--ambush", action="store_true", help="清空 ambush_watch")
    parser.add_argument("--heat-accum", action="store_true", help="清空 heat_accum_watch")
    parser.add_argument(
        "--all",
        action="store_true",
        help="同时清空 ambush_watch 与 heat_accum_watch",
    )
    args = parser.parse_args()

    if args.all:
        do_ambush = True
        do_heat = True
    elif args.ambush and args.heat_accum:
        do_ambush = True
        do_heat = True
    elif args.heat_accum:
        do_ambush = False
        do_heat = True
    elif args.ambush:
        do_ambush = True
        do_heat = False
    else:
        do_ambush = True
        do_heat = False

    os.chdir(_ROOT)
    from accumulation_radar import (
        DB_PATH,
        clear_ambush_watch_table,
        clear_heat_accum_watch_table,
        init_db,
    )

    db_path = Path(DB_PATH)
    conn = init_db()
    try:
        report = {}
        if do_ambush:
            report["ambush_watch"] = clear_ambush_watch_table(conn)
        if do_heat:
            report["heat_accum_watch"] = clear_heat_accum_watch_table(conn)
        if not report:
            parser.error("请指定 --ambush、--heat-accum 或 --all")
        print(f"数据库: {db_path}")
        for k, v in report.items():
            print(f"  已清空 {k}，删除前行数约 {v}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
