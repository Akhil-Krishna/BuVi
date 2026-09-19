"""A minimal MCP client over Streamable HTTP (JSON-RPC 2.0; MCP 2025-03-26 and later).

Only what the gateway needs: `initialize`, `tools/list`, `tools/call`. It is written here rather
than taken from the SDK so that every byte goes through `EgressClient`: resolved and checked at
request time, pinned, unredirected and capped. It is tested against the official SDK's server
in both response modes (a JSON body, or an SSE stream).

Nothing a server says is trusted or echoed:
* failures become fixed reason codes;
* tool output keeps text blocks and structured content only. Other block types (images, audio,
  embedded resources) are dropped and counted.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Final

import httpx

from mcp_gateway.domain.policies.tool_policy import DiscoveredTool
from mcp_gateway.infrastructure.mcp.egress import EgressClient, UpstreamFailure, content_type
from platform_egress import Endpoint, PinnedEndpoint

CLIENT_PROTOCOL: Final = "2025-06-18"
SUPPORTED_PROTOCOLS: Final = frozenset({"2025-03-26", "2025-06-18", "2025-11-25"})
CLIENT_INFO: Final = {"name": "buvi-mcp-gateway", "version": "0.1.0"}
_MAX_TOOL_PAGES: Final = 10


@dataclass(frozen=True)
class ToolResult:
    content: list[dict[str, Any]]
    structured_content: dict[str, Any] | None
    is_error: bool
    dropped_blocks: int
    size_bytes: int


@dataclass
class _Session:
    pinned: PinnedEndpoint
    token: str | None
    session_id: str | None = None
    protocol: str | None = None
    next_id: int = field(default=1)

    def headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol:
            headers["MCP-Protocol-Version"] = self.protocol
        return headers


class McpClient:
    def __init__(self, egress: EgressClient) -> None:
        self._egress = egress

    async def discover(self, endpoint: Endpoint, token: str | None) -> list[DiscoveredTool]:
        async with self._deadline(), self._session(endpoint, token) as session:
            tools: list[DiscoveredTool] = []
            cursor: str | None = None
            for _ in range(_MAX_TOOL_PAGES):
                result = await self._request(
                    session, "tools/list", {"cursor": cursor} if cursor else {}
                )
                tools.extend(_tool(item) for item in _list(result.get("tools")))
                cursor = (
                    result.get("nextCursor") if isinstance(result.get("nextCursor"), str) else None
                )
                if not cursor:
                    return tools
            raise UpstreamFailure("too_many_tool_pages")

    async def call(
        self, endpoint: Endpoint, token: str | None, name: str, arguments: dict[str, Any]
    ) -> ToolResult:
        async with self._deadline(), self._session(endpoint, token) as session:
            result, size = await self._request_sized(
                session, "tools/call", {"name": name, "arguments": arguments}
            )
        blocks = _list(result.get("content"))
        text = [
            {"type": "text", "text": block["text"]}
            for block in blocks
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ]
        structured = result.get("structuredContent")
        return ToolResult(
            content=text,
            structured_content=structured if isinstance(structured, dict) else None,
            is_error=result.get("isError") is True,
            dropped_blocks=len(blocks) - len(text),
            size_bytes=size,
        )

    @asynccontextmanager
    async def _deadline(self) -> AsyncIterator[None]:
        try:
            async with asyncio.timeout(self._egress.limits.timeout_seconds):
                yield
        except TimeoutError:
            raise UpstreamFailure("timeout") from None

    @asynccontextmanager
    async def _session(self, endpoint: Endpoint, token: str | None) -> AsyncIterator[_Session]:
        session = _Session(await self._egress.pin(endpoint), token)
        result = await self._request(
            session,
            "initialize",
            {"protocolVersion": CLIENT_PROTOCOL, "capabilities": {}, "clientInfo": CLIENT_INFO},
        )
        version = result.get("protocolVersion")
        capabilities = result.get("capabilities")
        if version not in SUPPORTED_PROTOCOLS:
            raise UpstreamFailure("protocol_version_unsupported")
        if not isinstance(capabilities, dict) or "tools" not in capabilities:
            raise UpstreamFailure("no_tools_capability")
        session.protocol = str(version)
        await self._notify(session, "notifications/initialized")
        try:
            yield session
        finally:
            if session.session_id:
                await self._egress.delete(session.pinned, session.headers())

    async def _notify(self, session: _Session, method: str) -> None:
        body = json.dumps({"jsonrpc": "2.0", "method": method}).encode()
        async with self._egress.post(session.pinned, body, session.headers()) as response:
            if response.status_code not in (200, 202, 204):
                raise UpstreamFailure(_status_reason(response.status_code))

    async def _request(
        self, session: _Session, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        result, _size = await self._request_sized(session, method, params)
        return result

    async def _request_sized(
        self, session: _Session, method: str, params: dict[str, Any]
    ) -> tuple[dict[str, Any], int]:
        request_id = session.next_id
        session.next_id += 1
        body = json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        ).encode()
        async with self._egress.post(session.pinned, body, session.headers()) as response:
            if response.status_code != 200:
                raise UpstreamFailure(_status_reason(response.status_code))
            if method == "initialize":
                session.session_id = _session_id(response)
            kind = content_type(response)
            if kind == "application/json":
                raw = await self._egress.read_body(response)
                message, size = _parse(raw), len(raw)
            elif kind == "text/event-stream":
                message, size = await self._read_event(response, request_id)
            else:
                raise UpstreamFailure("content_type_not_allowed")
        if not isinstance(message, dict) or message.get("id") != request_id:
            raise UpstreamFailure("protocol_error")
        if "error" in message:
            raise UpstreamFailure("jsonrpc_error")
        result = message.get("result")
        if not isinstance(result, dict):
            raise UpstreamFailure("protocol_error")
        return result, size

    async def _read_event(self, response: httpx.Response, request_id: int) -> tuple[Any, int]:
        """The SSE message answering `request_id`; server notifications before it are skipped."""
        data: list[str] = []
        size = 0
        async for line in self._egress.read_lines(response):
            size += len(line.encode()) + 1
            if line.startswith("data:"):
                data.append(line[5:].removeprefix(" "))
            elif line == "" and data:
                message = _parse("\n".join(data).encode())
                data = []
                if isinstance(message, dict) and message.get("id") == request_id:
                    return message, size
        if data:  # the stream ended without the blank line that closes an event
            message = _parse("\n".join(data).encode())
            if isinstance(message, dict) and message.get("id") == request_id:
                return message, size
        raise UpstreamFailure("protocol_error")


def _parse(raw: bytes) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        raise UpstreamFailure("protocol_error") from None


def _session_id(response: httpx.Response) -> str | None:
    value: str | None = response.headers.get("mcp-session-id")
    # Visible ASCII only (MCP spec); anything else is not echoed back in a header.
    if value and len(value) <= 256 and all(0x21 <= ord(c) <= 0x7E for c in value):
        return value
    return None


def _status_reason(status: int) -> str:
    if status in (401, 403):
        return "unauthorized"
    if status == 404:
        return "not_found"
    return "http_status"


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _tool(item: Any) -> DiscoveredTool:
    if not isinstance(item, dict) or not isinstance(item.get("name"), str):
        raise UpstreamFailure("protocol_error")
    annotations = item.get("annotations")
    hint = annotations.get("readOnlyHint") if isinstance(annotations, dict) else None
    return DiscoveredTool(
        name=item["name"], read_only_hint=hint if isinstance(hint, bool) else None
    )
