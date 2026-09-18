"""Internal routes (Section 6.3). Never routed by api-gateway.

* `GET /internal/v1/semantic-context?tenant_id=` -- the tenant's *approved* metrics (expression
  already parsed into aggregation + column) and its dimensions, for the Flow's `resolve_semantics`;
  `semantic-service:context`, callers in `context_readers` (analytics-orchestrator). The Flow
  matches base tables and columns against its own permitted context packet.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from platform_auth import ServiceIdentity, require_service_scope
from semantic_service.api.v1.schemas import (
    ContextDimension,
    ContextMetric,
    SemanticContextResponse,
)
from semantic_service.core.config import SCOPE_CONTEXT
from semantic_service.dependencies import get_app_settings
from semantic_service.domain.errors import ContextReaderNotAllowedError
from semantic_service.domain.policies.metric_expression import (
    MetricExpressionError,
    parse_expression,
)
from semantic_service.infrastructure.db.repositories.semantic_repository import (
    SemanticRepository,
)
from semantic_service.infrastructure.db.session import tenant_scope

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/internal/v1", tags=["internal"])


@router.get("/semantic-context", response_model=SemanticContextResponse)
async def semantic_context(
    request: Request,
    tenant_id: Annotated[uuid.UUID, Query()],
    service: Annotated[ServiceIdentity, Depends(require_service_scope(SCOPE_CONTEXT))],
) -> SemanticContextResponse:
    if service.subject not in get_app_settings(request).context_readers:
        raise ContextReaderNotAllowedError()
    async with tenant_scope(request.app.state.session_factory, tenant_id) as db:
        repository = SemanticRepository(db)
        metrics = await repository.approved_metrics(tenant_id)
        dimensions = await repository.all_dimensions(tenant_id)
        await db.commit()
    context_metrics = []
    for metric in metrics:
        try:
            parsed = parse_expression(metric.expression)
        except MetricExpressionError:
            # Stored expressions are normalized on write; an unparsable one is a defect.
            logger.error(
                "approved metric has an invalid expression",
                extra={"context": {"metric_id": str(metric.id)}},
            )
            continue
        context_metrics.append(
            ContextMetric(
                id=metric.id,
                name=metric.name,
                description=metric.description,
                synonyms=list(metric.synonyms),
                aggregation=parsed.aggregation,
                column=parsed.column,
                base_table_id=metric.base_table_id,
                default_grain=metric.default_grain,
            )
        )
    return SemanticContextResponse(
        tenant_id=tenant_id,
        metrics=context_metrics,
        dimensions=[
            ContextDimension(id=d.id, name=d.name, synonyms=list(d.synonyms), column_id=d.column_id)
            for d in dimensions
        ],
    )
