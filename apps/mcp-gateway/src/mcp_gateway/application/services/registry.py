"""Server registration, approval and tool grants (Sections 8.7, 14, 15; Phase A9)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from mcp_gateway.application.services.ports import McpServers
from mcp_gateway.domain.errors import (
    EndpointInvalidError,
    GrantExistsError,
    GrantInvalidError,
    GrantNotFoundError,
    InvalidTransitionError,
    ManifestInvalidError,
    ManifestMismatchError,
    NotFoundError,
    SecretStoreUnavailableError,
    ToolNotFoundError,
    ToolNotGrantableError,
    UpstreamError,
)
from mcp_gateway.domain.policies.endpoint import Endpoint, EndpointRejected, parse_endpoint
from mcp_gateway.domain.policies.tool_policy import (
    default_policy,
    is_grantable,
    manifest_problems,
    verification_problems,
)
from mcp_gateway.infrastructure.audit.sink import AuditRecord, AuditSink
from mcp_gateway.infrastructure.db.models import Server, Tool, ToolGrant
from mcp_gateway.infrastructure.db.repositories.mcp_repository import McpRepository
from mcp_gateway.infrastructure.mcp.egress import DestinationNotAllowedError, UpstreamFailure
from platform_auth import Principal
from platform_auth.permissions import TENANT_ROLES
from platform_egress import EgressPolicy
from platform_secrets import SecretStore, SecretStoreError

logger = logging.getLogger(__name__)


def secret_ref(tenant_id: uuid.UUID, server_id: uuid.UUID) -> str:
    return f"mcp/{tenant_id}/{server_id}"


async def server_token(secrets: SecretStore, server: Server) -> str | None:
    """The server's bearer token, read only at the moment it is sent."""
    if not server.auth_secret_ref:
        return None
    try:
        value = await secrets.read(server.auth_secret_ref)
    except SecretStoreError:
        raise SecretStoreUnavailableError() from None
    if not value or not value.get("token"):
        raise SecretStoreUnavailableError()
    return value["token"]


def stored_endpoint(server: Server, egress: EgressPolicy) -> Endpoint:
    """Re-checked on every use: the egress configuration may have changed since registration."""
    try:
        return parse_endpoint(server.endpoint_url, egress)
    except EndpointRejected as rejected:
        raise EndpointInvalidError(reason=rejected.reason) from None


@dataclass(frozen=True)
class NewServer:
    name: str
    endpoint_url: str
    auth_token: str | None
    tools: list[tuple[str, str]]


@dataclass(frozen=True)
class ServerDetail:
    server: Server
    tools: list[Tool]
    grants: list[ToolGrant]


class ServerRegistry:
    def __init__(
        self,
        *,
        repository: McpRepository,
        servers: McpServers,
        secrets: SecretStore,
        egress: EgressPolicy,
        audit: AuditSink,
        client_ip: str | None,
    ) -> None:
        self._repo = repository
        self._servers = servers
        self._secrets = secrets
        self._egress = egress
        self._audit = audit
        self._client_ip = client_ip

    async def register(self, principal: Principal, new: NewServer) -> ServerDetail:
        """`pending_approval`, with no network contact (Section 14)."""
        try:
            endpoint = parse_endpoint(new.endpoint_url, self._egress)
        except EndpointRejected as rejected:
            raise EndpointInvalidError(reason=rejected.reason) from None
        problems = manifest_problems(new.tools)
        if problems:
            raise ManifestInvalidError(problems=problems)
        tenant_id = uuid.UUID(principal.tenant_id)
        server_id = uuid.uuid4()
        ref = None
        if new.auth_token:
            ref = secret_ref(tenant_id, server_id)
            try:
                await self._secrets.write(ref, {"token": new.auth_token})
            except SecretStoreError:
                raise SecretStoreUnavailableError() from None
        server = Server(
            id=server_id,
            tenant_id=tenant_id,
            name=new.name,
            endpoint_url=endpoint.url,
            auth_secret_ref=ref,
            status="pending_approval",
            created_by=uuid.UUID(principal.user_id),
        )
        tools = [
            Tool(
                server_id=server_id,
                tool_name=name,
                tool_class=tool_class,
                default_policy=default_policy(tool_class),
            )
            for name, tool_class in new.tools
        ]
        try:
            await self._repo.add_server(server, tools)
        except Exception:
            if ref is not None:
                await self._forget_secret(ref)
            raise
        await self._record(
            principal,
            "mcp.server.registered",
            str(server_id),
            after={
                "name": new.name,
                "endpoint_url": endpoint.url,
                "has_auth_token": ref is not None,
                "tools": dict(new.tools),
            },
        )
        return ServerDetail(server, sorted(tools, key=lambda t: t.tool_name), [])

    async def list_servers(
        self, *, limit: int, after: uuid.UUID | None
    ) -> tuple[list[Server], uuid.UUID | None]:
        rows = await self._repo.list_servers(limit=limit + 1, after=after)
        page = rows[:limit]
        return page, page[-1].id if len(rows) > limit else None

    async def detail(self, server_id: uuid.UUID) -> ServerDetail:
        server = await self._repo.get_server(server_id)
        if server is None:
            raise NotFoundError()
        tools = await self._repo.tools(server_id)
        return ServerDetail(server, tools, await self._repo.grants([t.id for t in tools]))

    async def approve(self, principal: Principal, server_id: uuid.UUID) -> ServerDetail:
        """Discover the live tools, verify the declared manifest, then approve (Section 14).

        The network call runs outside any transaction; the status change is conditional on the
        server still being pending, so two concurrent approvals cannot both win.
        """
        before = await self.detail(server_id)
        if before.server.status != "pending_approval":
            raise InvalidTransitionError()
        endpoint = stored_endpoint(before.server, self._egress)
        token = await server_token(self._secrets, before.server)
        await self._repo.commit()
        try:
            discovered = await self._servers.discover(endpoint, token)
        except DestinationNotAllowedError:
            raise EndpointInvalidError(reason="destination_not_allowed") from None
        except UpstreamFailure as failure:
            raise UpstreamError(reason=failure.reason) from None
        problems = verification_problems(
            {t.tool_name: t.tool_class for t in before.tools}, discovered
        )
        if problems:
            raise ManifestMismatchError(problems=problems)
        if not await self._repo.approve(server_id, uuid.UUID(principal.user_id)):
            raise InvalidTransitionError()
        await self._record(
            principal,
            "mcp.server.approved",
            str(server_id),
            before={"status": "pending_approval"},
            after={
                "status": "approved",
                "tools": {t.tool_name: t.tool_class for t in before.tools},
                "undeclared_tools": sorted(
                    {d.name for d in discovered} - {t.tool_name for t in before.tools}
                )[:100],
            },
        )
        return await self.detail(server_id)

    async def grant(
        self,
        principal: Principal,
        server_id: uuid.UUID,
        tool_name: str,
        *,
        grantee_role: str | None,
        grantee_user_id: uuid.UUID | None,
    ) -> ToolGrant:
        if (grantee_role is None) == (grantee_user_id is None):
            raise GrantInvalidError()
        if grantee_role is not None and grantee_role not in TENANT_ROLES:
            raise GrantInvalidError(reason="unknown_role")
        tool = await self._tool(server_id, tool_name)
        if not is_grantable(tool.tool_class):
            raise ToolNotGrantableError()
        existing = await self._repo.grants([tool.id])
        if any(
            g.grantee_role == grantee_role and g.grantee_user_id == grantee_user_id
            for g in existing
        ):
            raise GrantExistsError()
        grant = await self._repo.add_grant(
            ToolGrant(
                tenant_id=uuid.UUID(principal.tenant_id),
                tool_id=tool.id,
                grantee_role=grantee_role,
                grantee_user_id=grantee_user_id,
                granted_by=uuid.UUID(principal.user_id),
            )
        )
        await self._record(
            principal,
            "mcp.tool.granted",
            str(tool.id),
            resource_type="mcp_tool",
            after={
                "grant_id": str(grant.id),
                "tool": tool_name,
                "grantee_role": grantee_role,
                "grantee_user_id": str(grantee_user_id) if grantee_user_id else None,
            },
        )
        return grant

    async def revoke(
        self, principal: Principal, server_id: uuid.UUID, tool_name: str, grant_id: uuid.UUID
    ) -> None:
        tool = await self._tool(server_id, tool_name)
        if not await self._repo.delete_grant(tool.id, grant_id):
            raise GrantNotFoundError()
        await self._record(
            principal,
            "mcp.tool.grant_revoked",
            str(tool.id),
            resource_type="mcp_tool",
            before={"grant_id": str(grant_id), "tool": tool_name},
        )

    async def _tool(self, server_id: uuid.UUID, tool_name: str) -> Tool:
        tool = await self._repo.tool(server_id, tool_name)
        if tool is None:
            raise ToolNotFoundError()
        return tool

    async def _forget_secret(self, ref: str) -> None:
        try:
            await self._secrets.delete(ref)
        except SecretStoreError:
            logger.error(
                "orphaned MCP secret could not be deleted", extra={"context": {"ref": ref}}
            )

    async def _record(
        self,
        principal: Principal,
        event_type: str,
        resource_id: str,
        *,
        resource_type: str = "mcp_server",
        before: dict[str, object] | None = None,
        after: dict[str, object] | None = None,
    ) -> None:
        await self._audit.record(
            AuditRecord(
                tenant_id=uuid.UUID(principal.tenant_id),
                actor_user_id=uuid.UUID(principal.user_id),
                event_type=event_type,
                resource_type=resource_type,
                resource_id=resource_id,
                before_state=before,
                after_state=after,
                ip_address=self._client_ip,
            )
        )
