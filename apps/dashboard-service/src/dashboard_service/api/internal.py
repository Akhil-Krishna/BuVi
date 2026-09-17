"""Internal routes (Section 6.3). Never routed by api-gateway.

* `POST /internal/v1/artifacts` -- store a run's artifact (Section 8.9: dashboard-service is the
  canonical store); `dashboard-service:artifacts`, callers in `artifact_writers`
  (analytics-orchestrator). Idempotent on `artifact_id`: `201` when stored, `200` when this run's
  artifact already exists, `409 ARTIFACT_CONFLICT` for another run. The chart spec is validated by
  visualization-service before anything is written (`422 CHART_SPEC_INVALID`).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from dashboard_service.api.v1.schemas import ArtifactCreateRequest, ArtifactStoredResponse
from dashboard_service.application.services.artifacts import ArtifactService, NewArtifact
from dashboard_service.core.config import SCOPE_ARTIFACTS_WRITE
from dashboard_service.dependencies import get_app_settings
from dashboard_service.domain.errors import ArtifactWriterNotAllowedError
from dashboard_service.infrastructure.db.repositories.dashboard_repository import (
    DashboardRepository,
)
from dashboard_service.infrastructure.db.session import tenant_scope
from platform_auth import ServiceIdentity, require_service_scope

router = APIRouter(prefix="/internal/v1", tags=["internal"])


@router.post(
    "/artifacts", response_model=ArtifactStoredResponse, status_code=status.HTTP_201_CREATED
)
async def store_artifact(
    request: Request,
    response: Response,
    payload: ArtifactCreateRequest,
    service: Annotated[ServiceIdentity, Depends(require_service_scope(SCOPE_ARTIFACTS_WRITE))],
) -> ArtifactStoredResponse:
    if service.subject not in get_app_settings(request).artifact_writers:
        raise ArtifactWriterNotAllowedError()
    state = request.app.state
    async with tenant_scope(state.session_factory, payload.tenant_id) as db:
        artifact, created = await ArtifactService(
            repository=DashboardRepository(db),
            visualization=state.visualization,
            results=state.results,
        ).store_from_run(
            NewArtifact(
                id=payload.artifact_id,
                tenant_id=payload.tenant_id,
                conversation_id=payload.conversation_id,
                run_id=payload.run_id,
                title=payload.title,
                summary=payload.summary,
                semantic_query=payload.semantic_query,
                source_refs=[ref.model_dump(mode="json") for ref in payload.source_refs],
                validated_sql=payload.validated_sql,
                query_result_ref=payload.query_result_ref,
                result_schema=payload.result_schema,
                chart_spec=payload.chart_spec,
                created_by=payload.created_by,
            )
        )
        await db.commit()
    if not created:
        response.status_code = status.HTTP_200_OK
    return ArtifactStoredResponse(
        artifact_id=artifact.id, version=artifact.version, created=created
    )
