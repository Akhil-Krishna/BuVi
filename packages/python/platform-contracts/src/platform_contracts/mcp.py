"""MCP wire contracts (Section 18.1)."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import Field

from platform_contracts.analytics import _Versioned


class McpInvocationDenied(_Versioned):
    """`mcp.invocation.denied`: mcp-gateway -> notification-service, audit (a security signal).

    Section 18.1's `{tool_id, tenant_id, reason}` plus the ids that let an alert be traced.
    Never carries tool arguments or output.
    """

    tenant_id: uuid.UUID
    tool_id: uuid.UUID
    reason: Literal[
        "server_not_approved",
        "tool_denied_by_policy",
        "not_granted",
        "admin_only",
        "step_up_required",
        "destination_not_allowed",
    ]
    invocation_id: uuid.UUID
    user_id: uuid.UUID
    request_id: str | None = Field(default=None, max_length=128)
