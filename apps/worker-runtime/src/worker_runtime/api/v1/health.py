"""Probes (Section 28). Ready: connected to NATS with both consumers running (runs, usage)."""

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
    checks = {
        name: "ok" if consumer is not None and consumer.connected else "unavailable"
        for name, consumer in (("queue", state.consumer), ("usage", state.usage_consumer))
    }
    ok = all(check == "ok" for check in checks.values())
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ok" if ok else "unavailable",
        service=state.settings.service_name,
        checks=checks,
    )
