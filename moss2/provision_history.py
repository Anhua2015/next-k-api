"""Moss2 全自动运维最近一次结果（供维护面板 / API 读取）。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _history_path() -> Path:
    data_dir = __import__("os").getenv("DATA_DIR", "").strip()
    if data_dir:
        root = Path(data_dir) / "moss2_auto"
    else:
        root = Path(__file__).resolve().parent.parent / "data" / "moss2_auto"
    root.mkdir(parents=True, exist_ok=True)
    return root / "last_provision.json"


def save_last_provision_run(
    stats: Dict[str, Any],
    *,
    trigger: str = "scheduler",
    bootstrap_context: Optional[str] = None,
) -> None:
    from moss2.auto_provision import format_provision_summary

    payload = {
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        "trigger": trigger,
        "bootstrap_context": bootstrap_context,
        "stats": stats,
        "summary_text": format_provision_summary(stats),
    }
    path = _history_path()
    try:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("[moss2] save last_provision failed: %s", exc)


def load_last_provision_run() -> Optional[Dict[str, Any]]:
    path = _history_path()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[moss2] load last_provision failed: %s", exc)
        return None
