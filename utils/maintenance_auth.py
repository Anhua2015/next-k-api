"""FastAPI 依赖：维护类路由鉴权。"""

from __future__ import annotations

from fastapi import Header, HTTPException

from utils.maintenance_token import (
    MaintenanceAuthError,
    maintenance_token_configured,
    verify_maintenance_token,
)

__all__ = [
    "MaintenanceAuthError",
    "maintenance_token_configured",
    "require_maintenance_token",
    "verify_maintenance_token",
]


async def require_maintenance_token(
    x_maintenance_token: str | None = Header(None, alias="X-Maintenance-Token"),
    authorization: str | None = Header(None),
) -> None:
    try:
        verify_maintenance_token(x_maintenance_token, authorization)
    except MaintenanceAuthError:
        raise HTTPException(
            status_code=401,
            detail="maintenance_token_required",
        ) from None
