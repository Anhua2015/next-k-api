"""Railway / uvicorn 进程内自动拉起 KK vnpy（无需 start.sh）。"""

from __future__ import annotations

import logging
import os
import threading
from threading import Event
from typing import Any, Dict, Optional

from orb.kk.config import KKConfig

logger = logging.getLogger(__name__)


def _autostart_enabled() -> bool:
    raw = (os.getenv("KK_VNPY_AUTO_START") or "1").strip().lower()
    return raw in ("1", "true", "yes", "on")


class KkVnpySupervisor:
    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop = Event()
        self._last_status: Dict[str, Any] = {}
        self._restart_count = 0

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def last_status(self) -> Dict[str, Any]:
        return dict(self._last_status)

    def should_start(self) -> bool:
        if not _autostart_enabled():
            return False
        if os.getenv("KK_VNPY_STANDALONE", "").strip().lower() in ("1", "true", "yes", "on"):
            logger.info("[kk-vnpy] supervisor skipped (KK_VNPY_STANDALONE=1，使用独立 kk_vnpy_runner)")
            return False
        kk = KKConfig.from_env()
        return bool(kk.enabled and kk.vnpy_enabled)

    def start(self) -> None:
        if not self.should_start():
            logger.info(
                "[kk-vnpy] supervisor skipped (KK_ENGINE=%s KK_ENABLED=%s AUTO_START=%s)",
                os.getenv("KK_ENGINE", "vnpy"),
                os.getenv("KK_ENABLED", "0"),
                os.getenv("KK_VNPY_AUTO_START", "1"),
            )
            return
        if self.is_running:
            logger.info("[kk-vnpy] supervisor already running")
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="kk-vnpy-supervisor",
            daemon=True,
        )
        self._thread.start()
        logger.info("[kk-vnpy] supervisor thread started")

    def stop(self, *, join_timeout: float = 45.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=join_timeout)
            self._thread = None

    def _run(self) -> None:
        restart_delay = max(5.0, float(os.getenv("KK_VNPY_RESTART_SEC") or 30))
        while not self._stop.is_set():
            engine = None
            try:
                from orb.kk.vnpy.runner import KkVnpyEngine

                init_wait = float(os.getenv("KK_VNPY_INIT_WAIT_SEC") or 30)
                engine = KkVnpyEngine()
                status = engine.bootstrap(init_wait_sec=init_wait)
                self._last_status = {**status, "restart_count": self._restart_count}
                if not status.get("ok"):
                    logger.error("[kk-vnpy] bootstrap failed: %s", status)
                    return
                if status.get("skipped"):
                    logger.info("[kk-vnpy] bootstrap skipped: %s", status.get("reason"))
                    return
                engine.run_until(self._stop)
            except ImportError as exc:
                logger.error(
                    "[kk-vnpy] 缺少 vnpy 依赖，请在 Railway 构建中安装 requirements-vnpy.txt: %s",
                    exc,
                )
                self._last_status = {"ok": False, "reason": "vnpy_import_error", "error": str(exc)}
                return
            except Exception as exc:
                logger.exception("[kk-vnpy] supervisor crashed: %s", exc)
                self._last_status = {"ok": False, "reason": "crash", "error": str(exc)}
            finally:
                if engine is not None:
                    try:
                        engine.shutdown()
                    except Exception as exc:
                        logger.warning("[kk-vnpy] shutdown: %s", exc)

            if self._stop.is_set():
                break
            self._restart_count += 1
            logger.warning("[kk-vnpy] restarting in %.0fs (count=%s)", restart_delay, self._restart_count)
            self._stop.wait(restart_delay)


kk_vnpy_supervisor = KkVnpySupervisor()
