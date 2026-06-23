#!/usr/bin/env python3
"""后台任务适配层。

调度器只调用本文件中的短函数。耗时扫描尽量放入独立 Python 子进程，原因是：

- 隔离扫描产生的内存、连接和第三方库状态；
- 防止长任务阻塞 FastAPI 主线程；
- 子进程异常不会直接终止 Web 服务；
- 每类任务使用独立非阻塞锁，避免上一轮未结束时重入。
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

_API_DIR = Path(__file__).resolve().parent
_RADAR_SCRIPT = _API_DIR / "accumulation_radar.py"
_S2_FUNDING_SCRIPT = _API_DIR / "s2_oi_funding_rate_scanner.py"
_ORB_SCRIPT = _API_DIR / "orb_scanner.py"

_subprocess_locks: Dict[str, threading.Lock] = {
    "accumulation_pool": threading.Lock(),
    "accumulation_oi": threading.Lock(),
    "s2_funding": threading.Lock(),
    "orb_scan": threading.Lock(),
    "orb_v2_monthly_train": threading.Lock(),
    "orb_ml_kline_refresh": threading.Lock(),
}
_heat_watch_refresh_lock = threading.Lock()


def _run_subprocess_locked(lock_key: str, argv: list[str], *, cwd: Path, env: dict | None = None) -> None:
    """在任务级互斥锁保护下同步等待子进程。

    此处的“同步等待”发生在 APScheduler 工作线程，不是 FastAPI 请求线程。若拿不到锁，
    直接跳过本轮而不是排队；市场扫描更需要“最新一轮”，通常不应积压过期任务。
    """
    lk = _subprocess_locks.get(lock_key)
    if lk is None:
        subprocess.run(argv, cwd=str(cwd), env=env, check=False)
        return
    if not lk.acquire(blocking=False):
        logger.warning(
            "跳过 %s：上一轮子进程仍在运行（本轮未执行）",
            lock_key,
        )
        return
    try:
        subprocess.run(argv, cwd=str(cwd), env=env, check=False)
    except Exception as e:
        logger.exception("%s subprocess failed: %s", lock_key, e)
    finally:
        lk.release()


def run_accumulation_radar_subprocess(mode: str) -> None:
    logger.info("Starting accumulation_radar subprocess mode=%s", mode)
    key = "accumulation_pool" if mode == "pool" else "accumulation_oi"
    _run_subprocess_locked(
        key,
        [sys.executable, str(_RADAR_SCRIPT), mode],
        cwd=_RADAR_SCRIPT.parent,
    )


def run_pool_task() -> None:
    logger.info("开始执行每日收筹池扫描...")
    run_accumulation_radar_subprocess("pool")


def run_oi_task() -> None:
    logger.info("开始执行每小时 OI 异动扫描...")
    run_accumulation_radar_subprocess("oi")


def refresh_heat_accum_watch_full_once() -> Dict[str, Any]:
    return _refresh_heat_accum_watch_full_once()


def _refresh_heat_accum_watch_full_once() -> Dict[str, Any]:
    from accumulation_radar import init_db, refresh_all_heat_accum_watch_full

    conn = init_db()
    try:
        return refresh_all_heat_accum_watch_full(conn)
    finally:
        conn.close()


def run_heat_watch_refresh_task() -> None:
    if not _heat_watch_refresh_lock.acquire(blocking=False):
        logger.info("热度看盘整表刷新跳过：已有任务在执行")
        return
    try:
        logger.info("开始执行热度看盘整表刷新...")
        data = _refresh_heat_accum_watch_full_once()
        logger.info(
            "热度看盘整表刷新完成: prices=%s",
            data.get("recalculated_prices"),
        )
    except Exception as e:
        logger.exception("heat watch refresh failed: %s", e)
    finally:
        _heat_watch_refresh_lock.release()


def run_s2_oi_funding_subprocess() -> None:
    logger.info("Starting s2_oi_funding_rate_scanner subprocess")
    _run_subprocess_locked(
        "s2_funding",
        [sys.executable, str(_S2_FUNDING_SCRIPT)],
        cwd=_S2_FUNDING_SCRIPT.parent,
    )


def run_s2_oi_funding_task() -> None:
    logger.info("开始执行 s2 OI+费率转负扫描...")
    run_s2_oi_funding_subprocess()


def _orb_scan_enabled() -> bool:
    from orb.v2.config import OrbV2Config
    from scheduler_config import ORB_V2_SCHEDULER_ENABLED

    if not ORB_V2_SCHEDULER_ENABLED:
        return False
    return OrbV2Config.from_env().enabled


def run_orb_scan_subprocess() -> None:
    logger.info("Starting orb_scanner subprocess")
    _run_subprocess_locked(
        "orb_scan",
        [sys.executable, str(_ORB_SCRIPT)],
        cwd=_ORB_SCRIPT.parent,
    )


def run_orb_scan_task() -> None:
    if not _orb_scan_enabled():
        from scheduler_config import ORB_V2_SCHEDULER_ENABLED

        if not ORB_V2_SCHEDULER_ENABLED:
            logger.info("ORB_V2_SCHEDULER_ENABLED=0，跳过 ORB 纸面扫描")
        else:
            logger.info("ORB_V2_ENABLED=0，跳过 ORB 纸面扫描")
        return
    run_orb_scan_subprocess()


def run_orb_v2_scan_task() -> None:
    """兼容旧 maintenance cron 名 orb_v2_scan。"""
    run_orb_scan_task()


def _orb_v2_monthly_train_enabled() -> bool:
    from scheduler_config import ORB_V2_MONTHLY_TRAIN_ENABLED

    return bool(ORB_V2_MONTHLY_TRAIN_ENABLED)


def run_orb_v2_monthly_train_subprocess() -> None:
    logger.info("Starting orb_v2_monthly_train subprocess")
    _run_subprocess_locked(
        "orb_v2_monthly_train",
        [sys.executable, str(_API_DIR / "tools" / "orb" / "v2" / "monthly_train.py")],
        cwd=_API_DIR,
    )


def run_orb_v2_monthly_train_task() -> None:
    if not _orb_v2_monthly_train_enabled():
        logger.info("ORB_V2_MONTHLY_TRAIN_ENABLED=0，跳过 ORB 月度训练")
        return
    run_orb_v2_monthly_train_subprocess()


def _orb_ml_kline_refresh_enabled() -> bool:
    from scheduler_config import ORB_ML_KLINE_REFRESH_ENABLED

    return bool(ORB_ML_KLINE_REFRESH_ENABLED)


def run_orb_ml_kline_refresh_subprocess() -> None:
    logger.info("Starting orb_ml_kline_refresh subprocess")
    _run_subprocess_locked(
        "orb_ml_kline_refresh",
        [sys.executable, str(_API_DIR / "tools" / "orb" / "v2" / "refresh_klines.py")],
        cwd=_API_DIR,
    )


def run_orb_ml_kline_refresh_task() -> None:
    if not _orb_ml_kline_refresh_enabled():
        logger.info("ORB_ML_KLINE_REFRESH_ENABLED=0，跳过 ORB K 线刷新")
        return
    run_orb_ml_kline_refresh_subprocess()


def heat_watch_refresh_lock() -> threading.Lock:
    return _heat_watch_refresh_lock
