"""Which event notifies whom, on which channels (Section 18.1; Phase A11). Pure.

Each consumed topic maps to one route. A route names its recipients either by user id (the
person the event is about) or by role (every active `org_admin` for a security signal), the
channels, and the template. The payload kept on a notification is the event minus its
`request_id` and `schema_version` -- ids and codes only, never a secret.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Final

from pydantic import BaseModel

from platform_contracts import (
    DashboardTilePinned,
    IdentityRoleChanged,
    McpInvocationDenied,
    MetadataSyncCompleted,
)

IN_APP: Final = "in_app"
EMAIL: Final = "email"
WEBHOOK: Final = "webhook"

#: Section 15 event-type allow-list for webhooks: what may leave the platform.
WEBHOOK_EVENT_TYPES: Final = frozenset(
    {"dashboard.tile.pinned", "metadata.sync.completed", "mcp.invocation.denied"}
)

#: subject -> (stream, contract). Streams are the producers' (`<domain>.>` each).
TOPICS: Final[dict[str, tuple[str, type[BaseModel]]]] = {
    "dashboard.tile.pinned": ("DASHBOARD", DashboardTilePinned),
    "mcp.invocation.denied": ("MCP", McpInvocationDenied),
    "metadata.sync.completed": ("METADATA", MetadataSyncCompleted),
    "identity.role.changed": ("IDENTITY", IdentityRoleChanged),
}


@dataclass(frozen=True)
class Route:
    template_key: str
    channels: tuple[str, ...]
    user_ids: tuple[uuid.UUID, ...] = ()
    role: str | None = None


def route_for(subject: str, event: Any) -> Route:
    if isinstance(event, DashboardTilePinned):
        return Route("dashboard.tile_pinned", (IN_APP,), user_ids=(event.user_id,))
    if isinstance(event, McpInvocationDenied):
        return Route("mcp.invocation_denied", (IN_APP, EMAIL), role="org_admin")
    if isinstance(event, MetadataSyncCompleted):
        return Route("metadata.sync_completed", (IN_APP, EMAIL), user_ids=(event.user_id,))
    if isinstance(event, IdentityRoleChanged):
        return Route("identity.role_changed", (IN_APP, EMAIL), user_ids=(event.user_id,))
    raise ValueError(f"no route for {subject}")


def notification_payload(event: BaseModel) -> dict[str, Any]:
    payload: dict[str, Any] = event.model_dump(
        mode="json", exclude={"request_id", "schema_version"}
    )
    return payload
