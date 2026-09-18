"""Tool invocation: policy, proxying and the security record (Sections 8.7, 14, 15, 21).

Order for a declared tool: server approved -> tool policy not `deny` -> a grant for the caller
-> the call, under the Section 15 request-time controls. Every attempt, allowed or denied,
becomes one `mcp.invocations` row and one audit event. A denial is also published as
`mcp.invocation.denied`. A tool that was never declared has no `tool_id` for that table, so the
attempt is audited only.

Output comes back to the caller marked untrusted and is never stored: the row keeps a summary
(status, block count, size). Arguments are stored, capped by `max_argument_bytes`.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, NoReturn

from mcp_gateway.application.services.ports import McpEvents, McpServers
from mcp_gateway.application.services.registry import server_token, stored_endpoint
from mcp_gateway.domain.errors import (
    ArgumentsTooLargeError,
    EndpointInvalidError,
    InvocationDeniedError,
    NotFoundError,
    SecretStoreUnavailableError,
    ToolNotFoundError,
    UpstreamError,
)
from mcp_gateway.domain.policies.tool_policy import DenialReason, GrantRef, denial
from mcp_gateway.infrastructure.audit.sink import AuditRecord, AuditSink
from mcp_gateway.infrastructure.db.models import Server, Tool
from mcp_gateway.infrastructure.db.repositories.mcp_repository import McpRepository
from mcp_gateway.infrastructure.mcp.client import ToolResult
from mcp_gateway.infrastructure.mcp.egress import DestinationNotAllowedError, UpstreamFailure
from platform_auth import Principal
from platform_contracts import McpInvocationDenied
from platform_egress import EgressPolicy
from platform_observability import request_id_var
from platform_secrets import SecretStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InvocationOutcome:
    invocation_id: uuid.UUID
    result: ToolResult


class InvocationService:
    def __init__(
        self,
        *,
        repository: McpRepository,
        servers: McpServers,
        secrets: SecretStore,
        egress: EgressPolicy,
        audit: AuditSink,
        events: McpEvents,
        max_argument_bytes: int,
        client_ip: str | None,
    ) -> None:
        self._repo = repository
        self._servers = servers
        self._secrets = secrets
        self._egress = egress
        self._audit = audit
        self._events = events
        self._max_argument_bytes = max_argument_bytes
        self._client_ip = client_ip

    async def invoke(
        self, principal: Principal, server_id: uuid.UUID, tool_name: str, arguments: dict[str, Any]
    ) -> InvocationOutcome:
        if len(json.dumps(arguments, separators=(",", ":"))) > self._max_argument_bytes:
            raise ArgumentsTooLargeError()
        server = await self._repo.get_server(server_id)
        if server is None:
            raise NotFoundError()
        tool = await self._repo.tool(server_id, tool_name)
        if tool is None:
            await self._audit_attempt(principal, server, tool_name, "denied", "unknown_tool")
            raise ToolNotFoundError()
        user_id = uuid.UUID(principal.user_id)
        grants = await self._repo.grants([tool.id])
        reason = denial(
            server_status=server.status,
            policy=tool.default_policy,
            grants=[GrantRef(g.grantee_role, g.grantee_user_id) for g in grants],
            user_id=user_id,
            roles=principal.roles,
        )
        payload = {"tool": tool_name, "arguments": arguments}
        if reason is not None:
            await self._deny(principal, server, tool, payload, reason, duration_ms=None)
        try:
            endpoint = stored_endpoint(server, self._egress)
        except EndpointInvalidError:  # the egress rules changed since approval
            await self._deny(principal, server, tool, payload, "destination_not_allowed", None)
        try:
            token = await server_token(self._secrets, server)
        except SecretStoreUnavailableError:
            await self._record(
                principal, server, tool, payload, "error", "secret_store_unavailable", None
            )
            raise
        await self._repo.commit()  # no transaction is held across the call

        started = time.perf_counter()
        try:
            result = await self._servers.call(endpoint, token, tool_name, arguments)
        except DestinationNotAllowedError:
            await self._deny(
                principal, server, tool, payload, "destination_not_allowed", _ms(started)
            )
        except UpstreamFailure as failure:
            await self._record(
                principal,
                server,
                tool,
                payload,
                "error",
                f"upstream:{failure.reason}",
                _ms(started),
            )
            raise UpstreamError(reason=failure.reason) from None
        status = "error" if result.is_error else "ok"
        summary = (
            f"{'tool_error' if result.is_error else 'ok'}: {len(result.content)} text block(s), "
            f"{result.dropped_blocks} dropped, {result.size_bytes} bytes"
        )
        invocation_id = await self._record(
            principal, server, tool, payload, status, summary, _ms(started)
        )
        return InvocationOutcome(invocation_id, result)

    async def _deny(
        self,
        principal: Principal,
        server: Server,
        tool: Tool,
        payload: dict[str, Any],
        reason: DenialReason,
        duration_ms: int | None,
    ) -> NoReturn:
        invocation_id = await self._record(
            principal, server, tool, payload, "denied", reason, duration_ms
        )
        try:
            await self._events.invocation_denied(
                McpInvocationDenied(
                    tenant_id=server.tenant_id,
                    tool_id=tool.id,
                    reason=reason,
                    invocation_id=invocation_id,
                    user_id=uuid.UUID(principal.user_id),
                    request_id=request_id_var.get(),
                )
            )
        except Exception as error:
            # The denial is already durable (row + audit); the alerting signal is best effort.
            logger.warning(
                "mcp denial event not published",
                extra={"context": {"reason": reason, "error_type": type(error).__name__}},
            )
        raise InvocationDeniedError(reason)

    async def _record(
        self,
        principal: Principal,
        server: Server,
        tool: Tool,
        payload: dict[str, Any],
        status: str,
        summary: str,
        duration_ms: int | None,
    ) -> uuid.UUID:
        invocation_id = await self._repo.record_invocation(
            tenant_id=server.tenant_id,
            tool_id=tool.id,
            invoked_by=principal.user_id,
            request_payload=payload,
            response_status=status,
            response_summary=summary,
            duration_ms=duration_ms,
        )
        await self._audit_attempt(
            principal, server, tool.tool_name, status, summary, invocation_id=invocation_id
        )
        return invocation_id

    async def _audit_attempt(
        self,
        principal: Principal,
        server: Server,
        tool_name: str,
        status: str,
        summary: str,
        *,
        invocation_id: uuid.UUID | None = None,
    ) -> None:
        await self._audit.record(
            AuditRecord(
                tenant_id=server.tenant_id,
                actor_user_id=uuid.UUID(principal.user_id),
                event_type="mcp.tool.denied" if status == "denied" else "mcp.tool.invoked",
                resource_type="mcp_server",
                resource_id=str(server.id),
                after_state={
                    "tool": tool_name,
                    "status": status,
                    "summary": summary,
                    "invocation_id": str(invocation_id) if invocation_id else None,
                },
                ip_address=self._client_ip,
            )
        )


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
