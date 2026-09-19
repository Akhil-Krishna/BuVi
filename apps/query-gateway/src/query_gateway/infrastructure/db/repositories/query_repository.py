"""Data access for `query_gateway.query_executions`: insert and read. Never update or delete --
the database refuses both for the request-path role."""

from __future__ import annotations

import uuid

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from query_gateway.infrastructure.db.models import QueryExecution


class QueryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    async def record(self, execution: QueryExecution) -> QueryExecution:
        self._session.add(execution)
        await self._session.flush()
        await self._session.refresh(execution)
        return execution

    async def get(self, tenant_id: uuid.UUID, execution_id: uuid.UUID) -> QueryExecution | None:
        result = await self._session.execute(
            select(QueryExecution).where(
                QueryExecution.tenant_id == tenant_id, QueryExecution.id == execution_id
            )
        )
        return result.scalar_one_or_none()

    async def history(
        self,
        tenant_id: uuid.UUID,
        *,
        purpose: str,
        requested_by: str | None,
        limit: int,
        before: uuid.UUID | None,
    ) -> list[QueryExecution]:
        """Newest first; `requested_by` None is the whole tenant (`run:debug`)."""
        statement = (
            select(QueryExecution)
            .where(QueryExecution.tenant_id == tenant_id, QueryExecution.purpose == purpose)
            .order_by(QueryExecution.created_at.desc(), QueryExecution.id.desc())
            .limit(limit)
        )
        if requested_by is not None:
            statement = statement.where(QueryExecution.requested_by == requested_by)
        if before is not None:
            anchor = (
                select(QueryExecution.created_at)
                .where(QueryExecution.tenant_id == tenant_id, QueryExecution.id == before)
                .scalar_subquery()
            )
            statement = statement.where(
                tuple_(QueryExecution.created_at, QueryExecution.id) < tuple_(anchor, before)
            )
        return list((await self._session.execute(statement)).scalars().all())
