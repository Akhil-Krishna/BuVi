"""Artifacts: stored by runs, read by users (Sections 8.9, 9.1, 16)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from dashboard_service.application.services.ports import ChartValidator, ResultReader
from dashboard_service.domain.errors import (
    ArtifactConflictError,
    ArtifactResultExpiredError,
    ChartSpecInvalidError,
    NotFoundError,
    UpstreamUnavailableError,
)
from dashboard_service.infrastructure.db.models import Artifact
from dashboard_service.infrastructure.db.repositories.dashboard_repository import (
    DashboardRepository,
)
from dashboard_service.infrastructure.http.clients import (
    DependencyUnavailableError,
    ResultGoneError,
    ResultNotFoundError,
    ResultRows,
)
from platform_auth import Principal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NewArtifact:
    id: uuid.UUID
    tenant_id: uuid.UUID
    conversation_id: uuid.UUID | None
    run_id: uuid.UUID
    title: str
    summary: str
    semantic_query: dict[str, Any]
    source_refs: list[dict[str, Any]]
    validated_sql: str
    query_result_ref: str
    result_schema: list[dict[str, Any]]
    chart_spec: dict[str, Any]
    created_by: uuid.UUID


class ArtifactService:
    def __init__(
        self,
        *,
        repository: DashboardRepository,
        visualization: ChartValidator,
        results: ResultReader,
    ) -> None:
        self._repository = repository
        self._visualization = visualization
        self._results = results

    async def store_from_run(self, new: NewArtifact) -> tuple[Artifact, bool]:
        """Idempotent on the artifact id the run derives from its own id: a resumed run gets the
        stored artifact back (`created` False); the same id for another run is a conflict."""
        existing = await self._repository.get_artifact(new.tenant_id, new.id)
        if existing is not None:
            if existing.run_id != new.run_id:
                raise ArtifactConflictError()
            return existing, False
        try:
            check = await self._visualization.check(new.chart_spec, new.result_schema)
        except DependencyUnavailableError:
            raise UpstreamUnavailableError() from None
        if not check.valid or check.chart_spec is None:
            raise ChartSpecInvalidError(problems=check.problems)
        artifact = await self._repository.add_artifact(
            Artifact(
                id=new.id,
                tenant_id=new.tenant_id,
                conversation_id=new.conversation_id,
                run_id=new.run_id,
                title=new.title,
                summary=new.summary,
                semantic_query=new.semantic_query,
                source_refs=new.source_refs,
                validated_sql=new.validated_sql,
                query_result_ref=new.query_result_ref,
                result_schema=new.result_schema,
                chart_spec=check.chart_spec,
                created_by=new.created_by,
            )
        )
        return artifact, True

    async def get(self, principal: Principal, artifact_id: uuid.UUID) -> Artifact:
        artifact = await self._repository.get_artifact(uuid.UUID(principal.tenant_id), artifact_id)
        if artifact is None:
            raise NotFoundError()
        return artifact

    async def data(self, principal: Principal, artifact_id: uuid.UUID) -> ResultRows:
        artifact = await self.get(principal, artifact_id)
        try:
            return await self._results.read(artifact.tenant_id, artifact.query_result_ref)
        except ResultGoneError:
            raise ArtifactResultExpiredError() from None
        except ResultNotFoundError:
            # The artifact exists but its handle is not a readable analytics result: a data
            # integrity problem, not the caller's -- log it, reveal nothing.
            logger.error(
                "artifact result handle not readable",
                extra={"context": {"artifact_id": str(artifact.id)}},
            )
            raise ArtifactResultExpiredError() from None
        except DependencyUnavailableError:
            raise UpstreamUnavailableError() from None
