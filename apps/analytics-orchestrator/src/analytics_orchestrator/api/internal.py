"""Internal routes (Section 6.3). Never routed by api-gateway.

* `POST /internal/v1/runs/{run_id}/execute?tenant_id=` -- run (or resume) the Flow to a terminal
  status; `analytics-orchestrator:execute` (worker-runtime). `409 RUN_BUSY` while another
  execution holds the run.
* `GET /internal/v1/runs/{run_id}/events?tenant_id=&after_seq=` -- persisted events for the SSE
  bridge; `analytics-orchestrator:events` (api-gateway). The tenant is the caller's authenticated
  principal's, so another tenant's run is `404`.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from analytics_orchestrator.api.v1.schemas import RunEventsResponse, RunStatusResponse
from analytics_orchestrator.core.config import SCOPE_EVENTS, SCOPE_EXECUTE
from analytics_orchestrator.domain.errors import NotFoundError
from analytics_orchestrator.infrastructure.db.repositories.analytics_repository import (
    AnalyticsRepository,
)
from analytics_orchestrator.infrastructure.db.session import tenant_scope
from platform_auth import ServiceIdentity, require_service_scope
from platform_contracts import AnalyticsRunEvent

router = APIRouter(prefix="/internal/v1", tags=["internal"])


@router.post("/runs/{run_id}/execute", response_model=RunStatusResponse)
async def execute_run(
    request: Request,
    run_id: uuid.UUID,
    tenant_id: Annotated[uuid.UUID, Query()],
    _service: Annotated[ServiceIdentity, Depends(require_service_scope(SCOPE_EXECUTE))],
) -> RunStatusResponse:
    outcome = await request.app.state.executor.execute(tenant_id, run_id)
    return RunStatusResponse(
        run_id=outcome.run_id, status=outcome.status, error_code=outcome.error_code
    )


@router.get("/runs/{run_id}/events", response_model=RunEventsResponse)
async def run_events(
    request: Request,
    run_id: uuid.UUID,
    tenant_id: Annotated[uuid.UUID, Query()],
    _service: Annotated[ServiceIdentity, Depends(require_service_scope(SCOPE_EVENTS))],
    after_seq: Annotated[int, Query(ge=0)] = 0,
) -> RunEventsResponse:
    async with tenant_scope(request.app.state.session_factory, tenant_id) as db:
        repository = AnalyticsRepository(db)
        run = await repository.get_run(tenant_id, run_id)
        if run is None:
            raise NotFoundError()
        events = await repository.list_events(run_id, after_seq=after_seq)
        await db.commit()
    return RunEventsResponse(
        run_id=run.id,
        status=run.status,
        events=[
            AnalyticsRunEvent(
                run_id=str(run.id),
                seq=e.seq,
                stage=e.stage,
                status=e.status,
                message=e.message,  # type: ignore[arg-type]
                artifact_id=str(e.artifact_id) if e.artifact_id else None,
                created_at=e.created_at,
            ).to_wire()
            for e in events
        ],
    )
