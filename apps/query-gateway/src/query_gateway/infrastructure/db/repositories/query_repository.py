"""Data access for `query_gateway.query_executions`: insert and read. Never update or delete --
the database refuses both for the request-path role."""

from __future__ import annotations

import uuid

from sqlalchemy import select
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
