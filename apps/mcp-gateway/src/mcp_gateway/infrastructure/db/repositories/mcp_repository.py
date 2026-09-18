"""Tenant-scoped persistence for the `mcp` schema. Every query runs under RLS (`tenant_scope`)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from mcp_gateway.infrastructure.db.models import Invocation, Server, Tool, ToolGrant


class McpRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def commit(self) -> None:
        """End the transaction, e.g. before a slow call to an MCP server."""
        await self._db.commit()

    async def add_server(self, server: Server, tools: Sequence[Tool]) -> Server:
        self._db.add(server)
        await self._db.flush()
        self._db.add_all(tools)
        await self._db.flush()
        await self._db.refresh(server)
        return server

    async def list_servers(self, *, limit: int, after: uuid.UUID | None) -> list[Server]:
        query = select(Server).order_by(Server.id).limit(limit)
        if after is not None:
            query = query.where(Server.id > after)
        return list((await self._db.scalars(query)).all())

    async def get_server(self, server_id: uuid.UUID) -> Server | None:
        server: Server | None = await self._db.get(Server, server_id, populate_existing=True)
        return server

    async def get_server_tenant_id(self, server_id: uuid.UUID) -> uuid.UUID | None:
        tenant_id: uuid.UUID | None = await self._db.scalar(
            select(Server.tenant_id).where(Server.id == server_id)
        )
        return tenant_id

    async def approve(self, server_id: uuid.UUID, approved_by: uuid.UUID) -> bool:
        """`pending_approval` -> `approved`, only if still pending (a concurrent approval loses)."""
        result = await self._db.execute(
            update(Server)
            .where(Server.id == server_id, Server.status == "pending_approval")
            .values(status="approved", approved_by=approved_by)
        )
        return bool(getattr(result, "rowcount", 0))

    async def tools(self, server_id: uuid.UUID) -> list[Tool]:
        return list(
            (
                await self._db.scalars(
                    select(Tool).where(Tool.server_id == server_id).order_by(Tool.tool_name)
                )
            ).all()
        )

    async def tool(self, server_id: uuid.UUID, name: str) -> Tool | None:
        tool: Tool | None = await self._db.scalar(
            select(Tool).where(Tool.server_id == server_id, Tool.tool_name == name)
        )
        return tool

    async def grants(self, tool_ids: Sequence[uuid.UUID]) -> list[ToolGrant]:
        if not tool_ids:
            return []
        return list(
            (
                await self._db.scalars(
                    select(ToolGrant)
                    .where(ToolGrant.tool_id.in_(tool_ids))
                    .order_by(ToolGrant.granted_at, ToolGrant.id)
                )
            ).all()
        )

    async def add_grant(self, grant: ToolGrant) -> ToolGrant:
        self._db.add(grant)
        await self._db.flush()
        await self._db.refresh(grant)
        return grant

    async def delete_grant(self, tool_id: uuid.UUID, grant_id: uuid.UUID) -> bool:
        result = await self._db.execute(
            delete(ToolGrant).where(ToolGrant.id == grant_id, ToolGrant.tool_id == tool_id)
        )
        return bool(getattr(result, "rowcount", 0))

    async def record_invocation(
        self,
        *,
        tenant_id: uuid.UUID,
        tool_id: uuid.UUID,
        invoked_by: str,
        request_payload: dict[str, Any],
        response_status: str,
        response_summary: str,
        duration_ms: int | None,
    ) -> uuid.UUID:
        row = Invocation(
            tenant_id=tenant_id,
            tool_id=tool_id,
            invoked_by=invoked_by,
            request_payload=request_payload,
            response_status=response_status,
            response_summary=response_summary,
            duration_ms=duration_ms,
        )
        self._db.add(row)
        await self._db.flush()
        await self._db.commit()  # a security record stands even if the request fails later
        return row.id
