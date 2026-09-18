"""Probes (Section 28). Ready requires Postgres."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

from semantic_service.api.v1.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=HealthResponse)
async def live(request: Request) -> HealthResponse:
    return HealthResponse(status="ok", service=request.app.state.settings.service_name)


@router.get("/health/ready", response_model=HealthResponse)
async def ready(request: Request, response: Response) -> HealthResponse:
    state = request.app.state
    try:
        async with state.session_factory() as session:
            await session.execute(text("SELECT 1"))
        database = "ok"
    except Exception:
        database = "unavailable"
    if database != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ok" if database == "ok" else "unavailable",
        service=state.settings.service_name,
        checks={"database": database},
    )
