"""Outbound ports of identity-service's application layer."""

from __future__ import annotations

from typing import Protocol

from platform_contracts import IdentityRoleChanged


class IdentityEvents(Protocol):
    """Section 18.1 events identity-service produces. Best effort: a failure is logged, never
    turned into a failed request (the change itself is committed and audited)."""

    async def role_changed(self, event: IdentityRoleChanged) -> None: ...
