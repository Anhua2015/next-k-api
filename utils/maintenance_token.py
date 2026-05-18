"""维护令牌校验（无 FastAPI 依赖，便于单测）。"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_warned_open_mode = False


class MaintenanceAuthError(PermissionError):
    """维护令牌缺失或错误。"""


def maintenance_token_configured() -> bool:
    return bool(os.getenv("NEXT_K_MAINTENANCE_TOKEN", "").strip())


def _extract_token(
    x_maintenance_token: str | None,
    authorization: str | None,
) -> str:
    if x_maintenance_token and str(x_maintenance_token).strip():
        return str(x_maintenance_token).strip()
    if authorization:
        parts = str(authorization).split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
    return ""


def verify_maintenance_token(
    x_maintenance_token: str | None = None,
    authorization: str | None = None,
) -> None:
    global _warned_open_mode
    expected = os.getenv("NEXT_K_MAINTENANCE_TOKEN", "").strip()
    if not expected:
        if not _warned_open_mode:
            logger.warning(
                "NEXT_K_MAINTENANCE_TOKEN 未设置：清库/trigger-cron/触轨池/VP 扫描等接口对公网开放；"
                "OI 刷新仅受 OI_RADAR_REFRESH_COOLDOWN_SEC 限制。生产请配置该变量"
            )
            _warned_open_mode = True
        return
    provided = _extract_token(x_maintenance_token, authorization)
    if not provided or provided != expected:
        raise MaintenanceAuthError("maintenance_token_required")
