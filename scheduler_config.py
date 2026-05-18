"""APScheduler 任务注册与开关（main 内嵌调度 / scheduler_main 共用）。"""

from __future__ import annotations

import os
from typing import Any

from apscheduler.triggers.interval import IntervalTrigger


def env_truthy(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name, "")
    if not str(raw).strip():
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


S6_FUTURES_ALPHA_SCHEDULER_ENABLED = env_truthy("S6_FUTURES_ALPHA_SCHEDULER_ENABLED")
ZCT_VWAP_SIGNAL_SCHEDULER_ENABLED = env_truthy("ZCT_VWAP_SIGNAL_SCHEDULER_ENABLED")
ZCT_VWAP_SCAN_INTERVAL_MINUTES = max(
    1, int(os.getenv("ZCT_VWAP_SCAN_INTERVAL_MINUTES", "12") or 12)
)
ZCT_VWAP_RESOLVE_INTERVAL_MINUTES = max(
    0, int(os.getenv("ZCT_VWAP_RESOLVE_INTERVAL_MINUTES", "5") or 5)
)


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
    sch.add_job(
        wt.run_zct_touch_pool_daily_task,
        "cron",
        hour=8,
        minute=0,
        id="zct_touch_pool_daily",
    )
    if S6_FUTURES_ALPHA_SCHEDULER_ENABLED:
        sch.add_job(
            wt.run_s6_futures_alpha_task,
            "cron",
            minute=25,
            id="s6_futures_alpha",
        )
    if ZCT_VWAP_SIGNAL_SCHEDULER_ENABLED:
        sch.add_job(
            wt.run_zct_vwap_signal_task,
            IntervalTrigger(minutes=ZCT_VWAP_SCAN_INTERVAL_MINUTES),
            id="zct_vwap_signal_scanner",
        )
        if ZCT_VWAP_RESOLVE_INTERVAL_MINUTES > 0:
            sch.add_job(
                wt.run_zct_vwap_resolve_only_task,
                IntervalTrigger(minutes=ZCT_VWAP_RESOLVE_INTERVAL_MINUTES),
                id="zct_vwap_resolve_only",
            )
