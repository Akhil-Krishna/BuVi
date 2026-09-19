"""Probes (Section 28). Ready requires Postgres, Redis (events, budgets) and the NATS run queue."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

from analytics_orchestrator.api.v1.schemas import HealthResponse

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
    try:
        checks["redis"] = "ok" if await state.redis.ping() else "unavailable"
    except Exception:
        checks["redis"] = "unavailable"
    checks["queue"] = "ok" if state.queue_ready() else "unavailable"
    serving = all(value == "ok" for value in checks.values())
    # Runs still work; billing is missing events until they are replayed (runbook).
    undelivered = getattr(getattr(state, "usage", None), "undelivered", 0)
    checks["usage"] = "ok" if not undelivered else f"degraded ({undelivered} undelivered)"
    if not serving:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status=("ok" if not undelivered else "degraded") if serving else "unavailable",
        service=state.settings.service_name,
        checks=checks,
    )
