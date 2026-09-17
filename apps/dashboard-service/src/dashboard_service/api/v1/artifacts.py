"""Artifact endpoints (Sections 9, 9.1, 16): `artifact:read` plus the resource-tenant check."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from dashboard_service.api.v1.schemas import (
    ArtifactDataResponse,
    ArtifactResponse,
    ResultColumnResponse,
    SourceRefResponse,
)
from dashboard_service.dependencies import (
    ScopedRepo,
    build_artifact_service,
    load_artifact_tenant_id,
)
from dashboard_service.infrastructure.db.models import Artifact
from platform_auth import Principal, require_permission, require_resource_owner
from platform_auth.permissions import PERM_ARTIFACT_READ, PERM_DASHBOARD_PIN

router = APIRouter(tags=["artifacts"])

ArtifactRead = Annotated[Principal, Depends(require_permission(PERM_ARTIFACT_READ))]
OwnsArtifact = Annotated[Principal, Depends(require_resource_owner(load_artifact_tenant_id))]


def artifact_response(artifact: Artifact, principal: Principal) -> ArtifactResponse:
    return ArtifactResponse(
        artifact_id=artifact.id,
        title=artifact.title,
        summary=artifact.summary,
        chart_spec=artifact.chart_spec,
        result_schema=artifact.result_schema,
        source_refs=[SourceRefResponse.model_validate(ref) for ref in artifact.source_refs],
        refresh_policy=artifact.refresh_policy,
        version=artifact.version,
        can_pin=principal.has_permission(PERM_DASHBOARD_PIN),
        conversation_id=artifact.conversation_id,
        run_id=artifact.run_id,
        created_at=artifact.created_at,
    )


@router.get("/artifacts/{artifact_id}", response_model=ArtifactResponse)
async def get_artifact(
    request: Request,
    artifact_id: uuid.UUID,
    principal: ArtifactRead,
    _owns: OwnsArtifact,
    repository: ScopedRepo,
) -> ArtifactResponse:
    artifact = await build_artifact_service(request, repository).get(principal, artifact_id)
    return artifact_response(artifact, principal)


@router.get("/artifacts/{artifact_id}/data", response_model=ArtifactDataResponse)
async def get_artifact_data(
    request: Request,
    artifact_id: uuid.UUID,
    principal: ArtifactRead,
    _owns: OwnsArtifact,
    repository: ScopedRepo,
) -> ArtifactDataResponse:
    """The stored result rows the chart renders; `410 ARTIFACT_RESULT_EXPIRED` after the TTL."""
    rows = await build_artifact_service(request, repository).data(principal, artifact_id)
    return ArtifactDataResponse(
        artifact_id=artifact_id,
        columns=[ResultColumnResponse(**column) for column in rows.columns],
        rows=rows.rows,
        row_count=rows.row_count,
        truncated=rows.truncated,
        expires_at=dt.datetime.fromisoformat(rows.expires_at),
    )
