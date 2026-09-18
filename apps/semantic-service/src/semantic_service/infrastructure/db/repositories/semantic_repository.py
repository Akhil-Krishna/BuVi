"""Data access for the `semantic` schema. Every query also runs under RLS for the session's tenant
(Section 19); the explicit `tenant_id` filters are the second layer, not the only one."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from semantic_service.infrastructure.db.models import Dimension, Metric


class DuplicateNameError(Exception):
    pass


class SemanticRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _insert(self, row: Metric | Dimension) -> None:
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError as error:
            raise DuplicateNameError() from error
        await self._session.refresh(row)

    # --- metrics -------------------------------------------------------------------------
    async def add_metric(self, metric: Metric) -> Metric:
        await self._insert(metric)
        return metric

    async def get_metric(
        self, tenant_id: uuid.UUID, metric_id: uuid.UUID, *, for_update: bool = False
    ) -> Metric | None:
        statement = select(Metric).where(Metric.tenant_id == tenant_id, Metric.id == metric_id)
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_metric_tenant_id(self, metric_id: uuid.UUID) -> uuid.UUID | None:
        result = await self._session.execute(select(Metric.tenant_id).where(Metric.id == metric_id))
        return result.scalar_one_or_none()

    async def list_metrics(
        self,
        tenant_id: uuid.UUID,
        *,
        status: str | None,
        limit: int,
        after: uuid.UUID | None,
    ) -> tuple[list[Metric], uuid.UUID | None]:
        statement = select(Metric).where(Metric.tenant_id == tenant_id)
        if status is not None:
            statement = statement.where(Metric.status == status)
        if after is not None:
            statement = statement.where(Metric.id > after)
        rows = list(
            (await self._session.execute(statement.order_by(Metric.id).limit(limit + 1)))
            .scalars()
            .all()
        )
        if len(rows) > limit:
            return rows[:limit], rows[limit - 1].id
        return rows, None

    async def approved_metrics(self, tenant_id: uuid.UUID) -> list[Metric]:
        result = await self._session.execute(
            select(Metric)
            .where(Metric.tenant_id == tenant_id, Metric.status == "approved")
            .order_by(Metric.name)
        )
        return list(result.scalars().all())

    async def flush(self) -> None:
        await self._session.flush()

    # --- dimensions ----------------------------------------------------------------------
    async def add_dimension(self, dimension: Dimension) -> Dimension:
        await self._insert(dimension)
        return dimension

    async def list_dimensions(
        self, tenant_id: uuid.UUID, *, limit: int, after: uuid.UUID | None
    ) -> tuple[list[Dimension], uuid.UUID | None]:
        statement = select(Dimension).where(Dimension.tenant_id == tenant_id)
        if after is not None:
            statement = statement.where(Dimension.id > after)
        rows = list(
            (await self._session.execute(statement.order_by(Dimension.id).limit(limit + 1)))
            .scalars()
            .all()
        )
        if len(rows) > limit:
            return rows[:limit], rows[limit - 1].id
        return rows, None

    async def all_dimensions(self, tenant_id: uuid.UUID) -> list[Dimension]:
        result = await self._session.execute(
            select(Dimension).where(Dimension.tenant_id == tenant_id).order_by(Dimension.name)
        )
        return list(result.scalars().all())
