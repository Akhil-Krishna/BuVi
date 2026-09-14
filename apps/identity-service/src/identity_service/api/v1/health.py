"""Liveness and readiness probes (Section 28).

`/health/live` answers "is the process up" and must never touch a dependency --
a database blip should not get a healthy pod killed. `/health/ready` answers
"can this instance serve traffic" and does check the database, because an
instance that cannot reach Postgres should be pulled from the load balancer.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from identity_service.api.v1.schemas import HealthResponse
from identity_service.core.config import Settings
from identity_service.dependencies import get_app_settings, get_session_factory

router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=HealthResponse)
async def live(settings: Annotated[Settings, Depends(get_app_settings)]) -> HealthResponse:
    return HealthResponse(status="ok", service=settings.service_name)


@router.get("/health/ready", response_model=HealthResponse)
async def ready(
    response: Response,
    settings: Annotated[Settings, Depends(get_app_settings)],
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
) -> HealthResponse:
    checks: dict[str, str] = {}
    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"

    healthy = all(value == "ok" for value in checks.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ok" if healthy else "degraded",
        service=settings.service_name,
        checks=checks,
    )
