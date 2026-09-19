"""Identity wire contracts (Section 18.1)."""

from __future__ import annotations

import uuid

from pydantic import Field

from platform_contracts.analytics import _Versioned


class IdentityRoleChanged(_Versioned):
    """`identity.role.changed`: identity-service -> notification-service. One event per change;
    `roles` is the user's role set afterwards."""

    tenant_id: uuid.UUID
    user_id: uuid.UUID
    roles: tuple[str, ...] = Field(max_length=10)
    granted: tuple[str, ...] = Field(max_length=10)
    revoked: tuple[str, ...] = Field(max_length=10)
    changed_by: uuid.UUID
    request_id: str | None = Field(default=None, max_length=128)
