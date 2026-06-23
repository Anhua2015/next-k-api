"""Next K API 进程入口。

本服务是整个系统的“策略与数据层”，职责包括：

1. 暴露收筹池、OI 雷达、S2 和 ORB 的查询/维护接口；
2. 启动或连接 APScheduler，按计划触发后台扫描；
3. 初始化 accumulation.db 和 ORB 生产模型包；
4. 在 ORB 产生可执行信号后，通过 HTTP 调用 Next-k-protocol。

注意：本进程不保存 Binance 密钥，也不直接调用需要签名的币安交易接口。
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager

from env_loader import load_env_oi

load_env_oi()

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app_state import state
import scheduler_config as sched_cfg
from scheduler_config import embed_scheduler_enabled
from routers import accumulation as accumulation_router
from routers import core as core_router
from routers import maintenance as maintenance_router
from routers import s2 as s2_router
from routers import orb as orb_router
import worker_tasks as wt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def _parse_cors_origins() -> list[str]:
    """解析浏览器跨域白名单。

    留空时返回 ``["*"]`` 是为了兼容当前静态前端和本地调试。CORS 只约束浏览器，
    不能替代维护令牌；脚本或服务端客户端不受浏览器 CORS 机制限制。
    """
    raw = os.getenv("NEXT_K_CORS_ORIGINS", "").strip()
    if not raw:
        return ["*"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


CORS_ORIGINS = _parse_cors_origins()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 生命周期。

    启动检查采用“失败降级、继续提供 API”的策略：数据库或模型检查异常只记 warning，
    避免一个非核心检查让健康接口完全不可用。真正执行 ORB 扫描时仍会再次验证生产模型，
    因此不会在模型缺失时静默开仓。
    """
    from datetime import datetime, timezone

    logger.info("Starting Next K API...")
    state.startup_time = datetime.now(timezone.utc)

    # 单进程部署时由 API 自己运行调度器；多 worker/双进程部署必须关闭它，
    # 否则每个 Web worker 都会重复注册同一批扫描任务。
    if embed_scheduler_enabled():
        _start_embedded_scheduler(app)
    else:
        logger.info(
            "Embedded scheduler off (NEXT_K_EMBED_SCHEDULER=0); "
            "run: python scheduler_main.py"
        )

    # 提前建表，使第一个前端请求不必承担数据库迁移延迟。
    try:
        from accumulation_radar import init_db

        conn = init_db()
        conn.close()
    except Exception as e:
        logger.warning("DB init on startup skipped: %s", e)

    # 仅打印高风险生产路径配置，例如误把运行模型指向可被 Volume 覆盖的 data/。
    try:
        from orb.ml.paths import production_env_warnings

        for msg in production_env_warnings():
            logger.warning("ORB production env: %s", msg)
    except Exception as e:
        logger.warning("ORB production env check skipped: %s", e)

    # orb_live/ 是生产运行包；必要时可从已存在的模型目录引导复制。
    try:
        from orb.ml.live_bundle import ensure_live_bundle_on_startup, log_live_bundle_startup

        copied = ensure_live_bundle_on_startup()
        if copied:
            logger.info("ORB live bundle bootstrapped on startup: %s", copied)
        log_live_bundle_startup()
    except Exception as e:
        logger.warning("ORB live bundle startup check skipped: %s", e)

    try:
        from orb.ml.model.paths import log_symbols_startup

        log_symbols_startup()
    except Exception as e:
        logger.warning("ORB symbols startup check skipped: %s", e)

    yield

    sch = getattr(app.state, "accumulation_scheduler", None)
    if sch is not None:
        sch.shutdown(wait=False)
        app.state.accumulation_scheduler = None
    logger.info("Shutting down...")


def _start_embedded_scheduler(app: FastAPI) -> None:
    """创建进程内调度器，并挂到 ``app.state`` 以便优雅关闭。"""
    import pytz

    tz = pytz.timezone("Asia/Shanghai")
    sch = BackgroundScheduler(timezone=tz)
    sched_cfg.register_scheduled_jobs(sch, wt)
    sch.start()
    app.state.accumulation_scheduler = sch
    logger.info("Embedded APScheduler started (Asia/Shanghai)")


app = FastAPI(
    title="Next K",
    description="OI radar, accumulation watchlists, ORB strategy API.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Maintenance-Token"],
)

app.include_router(core_router.router)
app.include_router(maintenance_router.router)
app.include_router(accumulation_router.router)
app.include_router(orb_router.router)
app.include_router(s2_router.router)


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
