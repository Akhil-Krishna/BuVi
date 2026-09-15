"""Liveness and readiness probes (Section 28).

`/health/live` never touches a dependency. `/health/ready` requires Postgres and
identity-service (without it no request can be authenticated). Vault is reported but
does not pull the instance from rotation: catalog reads keep working without it, and
credential operations fail with `503 SECRET_STORE_UNAVAILABLE`.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

from metadata_service.api.v1.schemas import HealthResponse

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
    checks["identity-service"] = "ok" if await state.identity.ready() else "unavailable"
    checks["secret-store"] = "ok" if await state.secrets.ping() else "unavailable"

    serving = checks["database"] == "ok" and checks["identity-service"] == "ok"
    if not serving:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    healthy = serving and checks["secret-store"] == "ok"
    return HealthResponse(
        status="ok" if healthy else ("degraded" if serving else "unavailable"),
        service=state.settings.service_name,
        checks=checks,
    )
