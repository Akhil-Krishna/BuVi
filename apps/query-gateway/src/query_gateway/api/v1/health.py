"""Probes (Section 28). Ready requires Postgres, identity-service and metadata-service -- a query
cannot be authenticated or validated without them. Vault and the result store are reported."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import text

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    service: str
    checks: dict[str, str] = Field(default_factory=dict)


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
    checks["identity-service"] = "ok" if await state.identity.ready() else "unavailable"
    checks["metadata-service"] = "ok" if await state.policies.ready() else "unavailable"
    checks["secret-store"] = "ok" if await state.secrets.ping() else "unavailable"
    checks["result-store"] = "ok" if await state.results.ping() else "unavailable"
    # Queries still run; billing is missing events until they are replayed (runbook).
    undelivered = getattr(state.usage, "undelivered", 0)
    checks["usage"] = "ok" if not undelivered else f"degraded ({undelivered} undelivered)"
    required = ("database", "identity-service", "metadata-service")
    serving = all(checks[name] == "ok" for name in required)
    if not serving:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    healthy = serving and all(v == "ok" for v in checks.values())
    return HealthResponse(
        status="ok" if healthy else ("degraded" if serving else "unavailable"),
        service=state.settings.service_name,
        checks=checks,
    )
