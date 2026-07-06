"""Railway / uvicorn 进程内自动拉起 Trading ORB vnpy。"""

from __future__ import annotations

import logging
import os
import threading
from threading import Event
from typing import Any, Dict, Optional

from orb.trading_orb.config import OrbVnpyConfig

logger = logging.getLogger(__name__)


def _autostart_enabled() -> bool:
    raw = (os.getenv("ORB_VNPY_AUTO_START") or "1").strip().lower()
    return raw in ("1", "true", "yes", "on")


class OrbVnpySupervisor:
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
        if os.getenv("ORB_VNPY_STANDALONE", "").strip().lower() in ("1", "true", "yes", "on"):
            logger.info("[orb-vnpy] supervisor skipped (ORB_VNPY_STANDALONE=1)")
            return False
        orb = OrbVnpyConfig.from_env()
        return bool(orb.enabled and orb.is_vnpy_engine())

    def start(self) -> None:
        if not self.should_start():
            logger.info(
                "[orb-vnpy] supervisor skipped (ORB_VNPY_ENABLED=%s AUTO_START=%s)",
                os.getenv("ORB_VNPY_ENABLED", os.getenv("ORB_ENABLED", "0")),
                os.getenv("ORB_VNPY_AUTO_START", "1"),
            )
            return
        if self.is_running:
            logger.info("[orb-vnpy] supervisor already running")
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="orb-vnpy-supervisor",
            daemon=True,
        )
        self._thread.start()
        logger.info("[orb-vnpy] supervisor thread started")

    def stop(self, *, join_timeout: float = 45.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=join_timeout)
            self._thread = None

    def _run(self) -> None:
        restart_delay = max(5.0, float(os.getenv("ORB_VNPY_RESTART_SEC") or 30))
        while not self._stop.is_set():
            engine = None
            try:
                from orb.trading_orb.vnpy.runner import OrbVnpyEngine

                init_wait = float(os.getenv("ORB_VNPY_INIT_WAIT_SEC") or 30)
                engine = OrbVnpyEngine()
                status = engine.bootstrap(init_wait_sec=init_wait)
                self._last_status = {**status, "restart_count": self._restart_count}
                if not status.get("ok"):
                    logger.error("[orb-vnpy] bootstrap failed: %s", status)
                    return
                if status.get("skipped"):
                    reason = str(status.get("reason") or "")
                    if reason == "macro_skip":
                        from orb.trading_orb.vnpy.session_util import seconds_until_next_session_open

                        orb = OrbVnpyConfig.from_env()
                        wait_sec = seconds_until_next_session_open(orb.orb_session_cfg())
                        logger.info(
                            "[orb-vnpy] macro_skip: wait %.0fs until next session",
                            wait_sec,
                        )
                        self._last_status = {
                            **status,
                            "restart_count": self._restart_count,
                            "wait_sec": wait_sec,
                        }
                        self._stop.wait(wait_sec)
                        continue
                    logger.info("[orb-vnpy] bootstrap skipped: %s", reason)
                    return
                engine.run_until(self._stop)
            except ImportError as exc:
                logger.error("[orb-vnpy] vnpy import failed: %s", exc)
                self._last_status = {"ok": False, "reason": "vnpy_import_error", "error": str(exc)}
                return
            except Exception as exc:
                logger.exception("[orb-vnpy] supervisor crashed: %s", exc)
                self._last_status = {"ok": False, "reason": "crash", "error": str(exc)}
            finally:
                if engine is not None:
                    try:
                        engine.shutdown()
                    except Exception as exc:
                        logger.warning("[orb-vnpy] shutdown: %s", exc)

            if self._stop.is_set():
                break
            self._restart_count += 1
            logger.warning(
                "[orb-vnpy] restarting in %.0fs (count=%s)",
                restart_delay,
                self._restart_count,
            )
            self._stop.wait(restart_delay)


orb_vnpy_supervisor = OrbVnpySupervisor()
