"""Outbound ports of identity-service's application layer."""

from __future__ import annotations

import uuid
from typing import Protocol

from platform_contracts import IdentityRoleChanged


class IdentityEvents(Protocol):
    """Section 18.1 events identity-service produces. Best effort: a failure is logged, never
    turned into a failed request (the change itself is committed and audited)."""

    async def role_changed(self, event: IdentityRoleChanged) -> None: ...


class CascadeFailedError(Exception):
    """A resource-owning service did not confirm its part of a deactivation."""


class UserLifecycle(Protocol):
    """The Section 6.7 cascade into other services. Must succeed before the deactivation
    commits: a share link is bearer access that would otherwise outlive the account."""

    async def user_deactivated(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> int:
        """Share links revoked. Raises `CascadeFailedError`."""
        ...
