"""Metric and dimension definitions (Sections 8.3, 12): create against the catalog, approve,
deprecate, and hand the approved set to the Flow."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from platform_auth import Principal
from semantic_service.domain.errors import (
    DefinitionInvalidError,
    InvalidTransitionError,
    NameTakenError,
    NotFoundError,
    UpstreamUnavailableError,
)
from semantic_service.domain.policies.metric_expression import (
    MetricExpressionError,
    MetricStatus,
    dimension_problems,
    metric_problems,
    next_status,
    parse_expression,
)
from semantic_service.infrastructure.audit.sink import AuditRecord, AuditSink
from semantic_service.infrastructure.db.models import Dimension, Metric
from semantic_service.infrastructure.db.repositories.semantic_repository import (
    DuplicateNameError,
    SemanticRepository,
)
from semantic_service.infrastructure.http.catalog_client import (
    CatalogLookup,
    CatalogUnavailableError,
)


class Catalog(Protocol):
    async def lookup(
        self,
        tenant_id: uuid.UUID,
        *,
        table_ids: list[uuid.UUID] | None = None,
        column_ids: list[uuid.UUID] | None = None,
    ) -> CatalogLookup: ...


@dataclass(frozen=True)
class NewMetric:
    name: str
    expression: str
    base_table_id: uuid.UUID
    description: str | None = None
    default_grain: str | None = None
    synonyms: list[str] = field(default_factory=list)


def _snapshot(metric: Metric) -> dict[str, object]:
    return {
        "name": metric.name,
        "expression": metric.expression,
        "base_table_id": str(metric.base_table_id),
        "status": metric.status,
    }


class DefinitionService:
    def __init__(
        self,
        *,
        repository: SemanticRepository,
        catalog: Catalog,
        audit: AuditSink,
        client_ip: str | None = None,
    ) -> None:
        self._repository = repository
        self._catalog = catalog
        self._audit = audit
        self._client_ip = client_ip

    @staticmethod
    def _ids(principal: Principal) -> tuple[uuid.UUID, uuid.UUID]:
        return uuid.UUID(principal.tenant_id), uuid.UUID(principal.user_id)

    async def _lookup(self, tenant_id: uuid.UUID, **ids: list[uuid.UUID]) -> CatalogLookup:
        try:
            return await self._catalog.lookup(tenant_id, **ids)
        except CatalogUnavailableError:
            raise UpstreamUnavailableError() from None

    async def _record(
        self,
        principal: Principal,
        event_type: str,
        metric: Metric,
        before: dict[str, object] | None,
    ) -> None:
        tenant_id, user_id = self._ids(principal)
        await self._audit.record(
            AuditRecord(
                tenant_id=tenant_id,
                actor_user_id=user_id,
                event_type=event_type,
                resource_type="semantic_metric",
                resource_id=str(metric.id),
                before_state=before,
                after_state=_snapshot(metric),
                ip_address=self._client_ip,
            )
        )

    # --- metrics -------------------------------------------------------------------------
    async def create_metric(self, principal: Principal, new: NewMetric) -> Metric:
        tenant_id, user_id = self._ids(principal)
        try:
            parsed = parse_expression(new.expression)
        except MetricExpressionError as error:
            raise DefinitionInvalidError(problems=[f"expression: {error}"]) from None
        lookup = await self._lookup(tenant_id, table_ids=[new.base_table_id])
        problems = metric_problems(parsed, lookup.tables.get(new.base_table_id))
        if problems:
            raise DefinitionInvalidError(problems=problems)
        try:
            metric = await self._repository.add_metric(
                Metric(
                    tenant_id=tenant_id,
                    name=new.name,
                    description=new.description,
                    expression=parsed.normalized,
                    default_grain=new.default_grain,
                    base_table_id=new.base_table_id,
                    synonyms=sorted({s.lower() for s in new.synonyms}),
                    created_by=user_id,
                )
            )
        except DuplicateNameError:
            raise NameTakenError() from None
        await self._record(principal, "semantic.metric.created", metric, None)
        return metric

    async def get_metric(self, principal: Principal, metric_id: uuid.UUID) -> Metric:
        tenant_id, _ = self._ids(principal)
        metric = await self._repository.get_metric(tenant_id, metric_id)
        if metric is None:
            raise NotFoundError()
        return metric

    async def list_metrics(
        self, principal: Principal, *, status: str | None, limit: int, cursor: uuid.UUID | None
    ) -> tuple[list[Metric], uuid.UUID | None]:
        tenant_id, _ = self._ids(principal)
        return await self._repository.list_metrics(
            tenant_id, status=status, limit=limit, after=cursor
        )

    async def transition(
        self, principal: Principal, metric_id: uuid.UUID, target: MetricStatus
    ) -> Metric:
        tenant_id, user_id = self._ids(principal)
        metric = await self._repository.get_metric(tenant_id, metric_id, for_update=True)
        if metric is None:
            raise NotFoundError()
        if not next_status(metric.status, target):  # type: ignore[arg-type]
            raise InvalidTransitionError(current=metric.status, target=target)
        before = _snapshot(metric)
        if target == "approved":
            # Re-check against today's catalog: a column may have become PII since the draft.
            lookup = await self._lookup(tenant_id, table_ids=[metric.base_table_id])
            problems = metric_problems(
                parse_expression(metric.expression), lookup.tables.get(metric.base_table_id)
            )
            if problems:
                raise DefinitionInvalidError(problems=problems)
            metric.approved_by = user_id
            metric.approved_at = dt.datetime.now(dt.UTC)
        metric.status = target
        await self._repository.flush()
        await self._record(principal, f"semantic.metric.{target}", metric, before)
        return metric

    # --- dimensions ----------------------------------------------------------------------
    async def create_dimension(
        self, principal: Principal, *, name: str, column_id: uuid.UUID, synonyms: list[str]
    ) -> Dimension:
        tenant_id, user_id = self._ids(principal)
        columns = await self._lookup(tenant_id, column_ids=[column_id])
        column = columns.columns.get(column_id)
        visible: bool | None = None
        if column is not None:
            tables = await self._lookup(tenant_id, table_ids=[column.table_id])
            visible = tables.table_visibility.get(column.table_id)
        problems = dimension_problems(column.ref if column else None, visible)
        if problems:
            raise DefinitionInvalidError(problems=problems)
        try:
            return await self._repository.add_dimension(
                Dimension(
                    tenant_id=tenant_id,
                    name=name,
                    column_id=column_id,
                    synonyms=sorted({s.lower() for s in synonyms}),
                    created_by=user_id,
                )
            )
        except DuplicateNameError:
            raise NameTakenError() from None

    async def list_dimensions(
        self, principal: Principal, *, limit: int, cursor: uuid.UUID | None
    ) -> tuple[list[Dimension], uuid.UUID | None]:
        tenant_id, _ = self._ids(principal)
        return await self._repository.list_dimensions(tenant_id, limit=limit, after=cursor)
