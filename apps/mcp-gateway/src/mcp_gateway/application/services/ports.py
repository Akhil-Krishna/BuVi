"""What the application needs from the outside world, as protocols (fakes in tests)."""

from __future__ import annotations

from typing import Any, Protocol

from mcp_gateway.domain.policies.tool_policy import DiscoveredTool
from mcp_gateway.infrastructure.mcp.client import ToolResult
from platform_contracts import McpInvocationDenied
from platform_egress import Endpoint


class McpServers(Protocol):
    """MCP over the Section 15 controls. Raises `platform_egress.DestinationNotAllowedError` or
    `UpstreamFailure` (infrastructure/mcp/egress.py)."""

    async def discover(self, endpoint: Endpoint, token: str | None) -> list[DiscoveredTool]: ...

    async def call(
        self, endpoint: Endpoint, token: str | None, name: str, arguments: dict[str, Any]
    ) -> ToolResult: ...


class McpEvents(Protocol):
    async def invocation_denied(self, event: McpInvocationDenied) -> None: ...
