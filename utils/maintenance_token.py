"""维护令牌校验（无 FastAPI 依赖，便于单测）。"""

from __future__ import annotations

import hmac
import logging
import os

logger = logging.getLogger(__name__)

_warned_open_mode = False


class MaintenanceAuthError(PermissionError):
    """维护令牌缺失或错误。"""


def _expected_token() -> str:
    """优先使用 API 专用令牌，兼容共用的 Protocol 令牌。"""
    return (
        os.getenv("NEXT_K_MAINTENANCE_TOKEN", "").strip()
        or os.getenv("PROTOCOL_MAINTENANCE_TOKEN", "").strip()
    )


def maintenance_token_configured() -> bool:
    return bool(_expected_token())


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
    expected = _expected_token()
    if not expected:
        if not _warned_open_mode:
            logger.warning(
                "NEXT_K_MAINTENANCE_TOKEN / PROTOCOL_MAINTENANCE_TOKEN 未设置："
                "维护写接口处于兼容开放模式；生产环境请配置令牌"
            )
            _warned_open_mode = True
        return

    provided = _extract_token(x_maintenance_token, authorization)
    if not provided or not hmac.compare_digest(provided, expected):
        raise MaintenanceAuthError("maintenance_token_required")
