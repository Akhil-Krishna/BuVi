"""Probes (Section 28). Ready requires identity-service, since nothing protected can
be authenticated without it; Redis is reported but, with fail-open limiting, does not
take the gateway out of rotation."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field

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
    identity_ok = await state.identity.ready()
    redis_ok = await state.rate_limiter.ping()
    checks = {
        "identity-service": "ok" if identity_ok else "unavailable",
        "redis": "ok" if redis_ok else "unavailable",
    }
    serving = identity_ok and (redis_ok or state.settings.rate_limit_fail_open)
    if not serving:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ok" if serving and redis_ok else ("degraded" if serving else "unavailable"),
        service=state.settings.service_name,
        checks=checks,
    )
