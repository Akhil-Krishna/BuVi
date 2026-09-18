"""Metric and dimension endpoints (Section 9): `semantic:manage` plus the resource-tenant check."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, status

from platform_auth import Principal, require_permission, require_resource_owner
from platform_auth.permissions import PERM_SEMANTIC_MANAGE
from semantic_service.api.v1.schemas import (
    DimensionCreateRequest,
    DimensionListResponse,
    DimensionResponse,
    MetricCreateRequest,
    MetricListResponse,
    MetricResponse,
)
from semantic_service.application.services.definitions import NewMetric
from semantic_service.dependencies import (
    ScopedRepo,
    build_definition_service,
    load_metric_tenant_id,
)
from semantic_service.domain.errors import InvalidCursorError
from semantic_service.infrastructure.db.models import Dimension, Metric

router = APIRouter(tags=["semantic"])

SemanticManage = Annotated[Principal, Depends(require_permission(PERM_SEMANTIC_MANAGE))]
OwnsMetric = Annotated[Principal, Depends(require_resource_owner(load_metric_tenant_id))]
Limit = Annotated[int, Query(ge=1, le=200)]
Cursor = Annotated[str | None, Query(max_length=64)]


def _cursor(cursor: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(cursor) if cursor else None
    except ValueError:
        raise InvalidCursorError() from None


def _metric(metric: Metric) -> MetricResponse:
    return MetricResponse.model_validate(metric, from_attributes=True)


def _dimension(dimension: Dimension) -> DimensionResponse:
    return DimensionResponse.model_validate(dimension, from_attributes=True)


@router.get("/semantic/metrics", response_model=MetricListResponse)
async def list_metrics(
    request: Request,
    principal: SemanticManage,
    repository: ScopedRepo,
    status_filter: Annotated[
        Literal["draft", "approved", "deprecated"] | None, Query(alias="status")
    ] = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> MetricListResponse:
    rows, next_key = await build_definition_service(request, repository).list_metrics(
        principal, status=status_filter, limit=limit, cursor=_cursor(cursor)
    )
    return MetricListResponse(
        items=[_metric(m) for m in rows], next_cursor=str(next_key) if next_key else None
    )


@router.post(
    "/semantic/metrics", response_model=MetricResponse, status_code=status.HTTP_201_CREATED
)
async def create_metric(
    request: Request,
    payload: MetricCreateRequest,
    principal: SemanticManage,
    repository: ScopedRepo,
) -> MetricResponse:
    metric = await build_definition_service(request, repository).create_metric(
        principal,
        NewMetric(
            name=payload.name,
            expression=payload.expression,
            base_table_id=payload.base_table_id,
            description=payload.description,
            default_grain=payload.default_grain,
            synonyms=payload.synonyms,
        ),
    )
    return _metric(metric)


@router.get("/semantic/metrics/{metric_id}", response_model=MetricResponse)
async def get_metric(
    request: Request,
    metric_id: uuid.UUID,
    principal: SemanticManage,
    _owns: OwnsMetric,
    repository: ScopedRepo,
) -> MetricResponse:
    return _metric(
        await build_definition_service(request, repository).get_metric(principal, metric_id)
    )


@router.post("/semantic/metrics/{metric_id}/approve", response_model=MetricResponse)
async def approve_metric(
    request: Request,
    metric_id: uuid.UUID,
    principal: SemanticManage,
    _owns: OwnsMetric,
    repository: ScopedRepo,
) -> MetricResponse:
    """`draft` -> `approved`: only approved metrics reach the Flow. Re-checked and audited."""
    return _metric(
        await build_definition_service(request, repository).transition(
            principal, metric_id, "approved"
        )
    )


@router.post("/semantic/metrics/{metric_id}/deprecate", response_model=MetricResponse)
async def deprecate_metric(
    request: Request,
    metric_id: uuid.UUID,
    principal: SemanticManage,
    _owns: OwnsMetric,
    repository: ScopedRepo,
) -> MetricResponse:
    return _metric(
        await build_definition_service(request, repository).transition(
            principal, metric_id, "deprecated"
        )
    )


@router.get("/semantic/dimensions", response_model=DimensionListResponse)
async def list_dimensions(
    request: Request,
    principal: SemanticManage,
    repository: ScopedRepo,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> DimensionListResponse:
    rows, next_key = await build_definition_service(request, repository).list_dimensions(
        principal, limit=limit, cursor=_cursor(cursor)
    )
    return DimensionListResponse(
        items=[_dimension(d) for d in rows], next_cursor=str(next_key) if next_key else None
    )


@router.post(
    "/semantic/dimensions", response_model=DimensionResponse, status_code=status.HTTP_201_CREATED
)
async def create_dimension(
    request: Request,
    payload: DimensionCreateRequest,
    principal: SemanticManage,
    repository: ScopedRepo,
) -> DimensionResponse:
    dimension = await build_definition_service(request, repository).create_dimension(
        principal, name=payload.name, column_id=payload.column_id, synonyms=payload.synonyms
    )
    return _dimension(dimension)
