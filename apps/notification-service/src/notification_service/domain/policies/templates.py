"""Notification text, rendered from the stored payload (Phase A11). Pure.

In-app items and emails share one rendering, so what a user reads in the inbox is what was
mailed. Templates use ids and codes from the payload; nothing user-supplied is interpreted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Rendered:
    title: str
    body: str


def _roles(values: Any) -> str:
    return ", ".join(values) if values else "none"


def render(template_key: str, payload: dict[str, Any]) -> Rendered:
    if template_key == "dashboard.tile_pinned":
        return Rendered(
            "Pinned to your dashboard",
            f"An artifact was pinned to dashboard {payload.get('dashboard_id')}.",
        )
    if template_key == "mcp.invocation_denied":
        return Rendered(
            "MCP tool call denied",
            f"A call to MCP tool {payload.get('tool_id')} was denied "
            f"(reason: {payload.get('reason')}). Invocation {payload.get('invocation_id')}.",
        )
    if template_key == "metadata.sync_completed":
        name = payload.get("data_source_name")
        if payload.get("status") == "succeeded":
            return Rendered(
                "Schema sync finished",
                f"The schema sync of {name} finished: {payload.get('tables_synced')} tables.",
            )
        return Rendered(
            "Schema sync failed", f"The schema sync of {name} failed. Check the connection."
        )
    if template_key == "identity.role_changed":
        return Rendered(
            "Your roles changed",
            f"Your roles are now: {_roles(payload.get('roles'))}. "
            f"Granted: {_roles(payload.get('granted'))}. "
            f"Revoked: {_roles(payload.get('revoked'))}.",
        )
    return Rendered("Notification", "You have a new notification.")
