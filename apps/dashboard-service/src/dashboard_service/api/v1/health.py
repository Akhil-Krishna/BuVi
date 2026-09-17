"""Probes (Section 28). Ready requires Postgres; the event stream is reported, not required."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

from dashboard_service.api.v1.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=HealthResponse)
async def live(request: Request) -> HealthResponse:
    return HealthResponse(status="ok", service=request.app.state.settings.service_name)


@router.get("/health/ready", response_model=HealthResponse)
async def ready(request: Request, response: Response) -> HealthResponse:
    state = request.app.state
    checks: dict[str, str] = {}
    try:
        async with state.session_factory() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"
    checks["events"] = "ok" if state.events_ready() else "degraded"
    serving = checks["database"] == "ok"
    if not serving:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ok" if serving else "unavailable",
        service=state.settings.service_name,
        checks=checks,
    )
