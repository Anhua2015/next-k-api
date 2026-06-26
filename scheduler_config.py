"""APScheduler 任务注册与开关（main 内嵌调度 / scheduler_main 共用）。"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import pytz
from apscheduler.triggers.interval import IntervalTrigger

from orb.core.config import default_scan_interval_minutes

logger = logging.getLogger(__name__)

ORB_SCAN_CRON_TZ = pytz.UTC
ORB_PREMARKET_KLINE_TZ = pytz.timezone("America/New_York")


def env_truthy(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name, "")
    if not str(raw).strip():
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def embed_scheduler_enabled() -> bool:
    """API 进程内嵌 APScheduler；未设 env 时默认开启。设 NEXT_K_EMBED_SCHEDULER=0 关闭。"""
    return env_truthy("NEXT_K_EMBED_SCHEDULER", default=True)


ORB_V2_SCHEDULER_ENABLED = env_truthy("ORB_V2_SCHEDULER_ENABLED", default=True)
ORB_V2_MONTHLY_TRAIN_ENABLED = env_truthy("ORB_V2_MONTHLY_TRAIN_ENABLED", default=False)
ORB_ML_KLINE_REFRESH_ENABLED = env_truthy("ORB_ML_KLINE_REFRESH_ENABLED", default=True)


def _int_env_orb_scan_interval() -> int:
    raw = os.getenv("ORB_SCAN_INTERVAL_MINUTES")
    if raw is not None and str(raw).strip():
        try:
            return int(float(str(raw).strip()))
        except ValueError:
            logging.getLogger(__name__).warning(
                "Invalid ORB_SCAN_INTERVAL_MINUTES=%r, using default", raw
            )
    return default_scan_interval_minutes()


ORB_SCAN_INTERVAL_MINUTES = max(1, _int_env_orb_scan_interval())


def _int_env_orb_scan_cron_second() -> int:
    raw = os.getenv("ORB_SCAN_CRON_SECOND", "5")
    try:
        return max(0, min(59, int(float(str(raw).strip()))))
    except ValueError:
        logger.warning("Invalid ORB_SCAN_CRON_SECOND=%r, using 5", raw)
        return 5


ORB_SCAN_CRON_SECOND = _int_env_orb_scan_cron_second()


def orb_scan_cron_kwargs(interval_minutes: int, *, second: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Binance K 线 UTC 边界对齐的 cron 参数；interval 须能整除 60。"""
    n = max(1, int(interval_minutes))
    if 60 % n != 0:
        return None
    sec = ORB_SCAN_CRON_SECOND if second is None else max(0, min(59, int(second)))
    if n == 1:
        return {"minute": "*", "second": sec, "timezone": ORB_SCAN_CRON_TZ}
    return {"minute": f"*/{n}", "second": sec, "timezone": ORB_SCAN_CRON_TZ}


def orb_or_arm_cron_kwargs() -> Optional[Dict[str, Any]]:
    """美东 RTH：session_open + OR 分钟数 → OR 结束时刻触发 scan（preplace 武装）。"""
    from orb.core.config import OrbConfig

    c = OrbConfig.from_env()
    if not c.arm_at_or_close:
        return None
    open_t = (c.session_open_time or "09:30").strip()
    if not open_t:
        return None
    parts = open_t.split(":")
    try:
        oh = int(parts[0])
        om = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        logger.warning("Invalid ORB_SESSION_OPEN=%r for or_arm cron", open_t)
        return None
    total_min = oh * 60 + om + max(1, int(c.or_minutes))
    arm_h = (total_min // 60) % 24
    arm_m = total_min % 60
    tz_name = (c.session_tz or "America/New_York").strip()
    try:
        tz = pytz.timezone(tz_name)
    except Exception:
        tz = ORB_PREMARKET_KLINE_TZ
    return {
        "hour": arm_h,
        "minute": arm_m,
        "second": ORB_SCAN_CRON_SECOND,
        "timezone": tz,
        "day_of_week": "mon-fri",
    }


def register_scheduled_jobs(sch: Any, wt: Any) -> None:
    """向 BackgroundScheduler / BlockingScheduler 注册与 worker_tasks 对齐的 cron。"""
    sch.add_job(wt.run_pool_task, "cron", hour=10, minute=0, id="pool_daily")
    sch.add_job(
        wt.run_heat_watch_refresh_task,
        "cron",
        minute=7,
        id="heat_watch_refresh",
    )
    sch.add_job(wt.run_oi_task, "cron", minute=30, id="oi_hourly")
    sch.add_job(wt.run_s2_oi_funding_task, "cron", minute=5, id="s2_oi_funding")
    if ORB_V2_SCHEDULER_ENABLED:
        cron_kw = orb_scan_cron_kwargs(ORB_SCAN_INTERVAL_MINUTES)
        if cron_kw:
            sch.add_job(
                wt.run_orb_scan_task,
                "cron",
                id="orb_scanner",
                replace_existing=True,
                **cron_kw,
            )
            or_arm_kw = orb_or_arm_cron_kwargs()
            if or_arm_kw:
                sch.add_job(
                    wt.run_orb_scan_task,
                    "cron",
                    id="orb_or_arm",
                    replace_existing=True,
                    **or_arm_kw,
                )
                logger.info(
                    "orb_or_arm cron: %02d:%02d:%02d %s (preplace at OR close)",
                    or_arm_kw["hour"],
                    or_arm_kw["minute"],
                    or_arm_kw["second"],
                    or_arm_kw["timezone"],
                )
        else:
            logger.warning(
                "ORB_SCAN_INTERVAL_MINUTES=%s 无法对齐 UTC 整分 cron（须整除 60），"
                "回退 IntervalTrigger；建议设为 1/2/3/5/15 等",
                ORB_SCAN_INTERVAL_MINUTES,
            )
            sch.add_job(
                wt.run_orb_scan_task,
                IntervalTrigger(minutes=ORB_SCAN_INTERVAL_MINUTES),
                id="orb_scanner",
                replace_existing=True,
            )
            or_arm_kw = orb_or_arm_cron_kwargs()
            if or_arm_kw:
                sch.add_job(
                    wt.run_orb_scan_task,
                    "cron",
                    id="orb_or_arm",
                    replace_existing=True,
                    **or_arm_kw,
                )
                logger.info(
                    "orb_or_arm cron: %02d:%02d:%02d %s (preplace at OR close)",
                    or_arm_kw["hour"],
                    or_arm_kw["minute"],
                    or_arm_kw["second"],
                    or_arm_kw["timezone"],
                )
    if ORB_V2_MONTHLY_TRAIN_ENABLED:
        sch.add_job(
            wt.run_orb_v2_monthly_train_task,
            "cron",
            day=1,
            hour=3,
            minute=0,
            id="orb_v2_monthly_train",
            replace_existing=True,
        )
    if ORB_ML_KLINE_REFRESH_ENABLED:
        sch.add_job(
            wt.run_orb_ml_kline_refresh_task,
            "cron",
            day=1,
            hour=2,
            minute=0,
            id="orb_ml_kline_refresh",
            replace_existing=True,
        )
        # 美股开盘前刷新 K 线缓存（09:25 ET），供突破分 ATR/量均线
        sch.add_job(
            wt.run_orb_ml_kline_refresh_task,
            "cron",
            day_of_week="mon-fri",
            hour=9,
            minute=25,
            timezone=ORB_PREMARKET_KLINE_TZ,
            id="orb_ml_kline_refresh_premarket",
            replace_existing=True,
        )
