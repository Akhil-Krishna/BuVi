"""Writes that must survive the request's rollback (Sections 7.3, 22; Phase A10).

A refused MFA check raises, and the request's transaction rolls back with everything it wrote.
Two writes must stand anyway:

* the audit event recording the failure (a security signal, Section 22);
* spending the WebAuthn challenge, so a failed attempt cannot be retried against it.

Each runs in its own short transaction on its own connection, bound to the tenant and
cleared by `tenant_scope`. It never commits the request's session mid-flight: that session
binds its tenant with a session-level setting, and a mid-request commit would hand a pooled
connection back still bound to this tenant.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from identity_service.infrastructure.db.repositories.identity_repository import (
    IdentityRepository,
)
from identity_service.infrastructure.db.session import tenant_scope

T = TypeVar("T")


class IndependentWrites:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def run(
        self, tenant_id: uuid.UUID, work: Callable[[IdentityRepository], Awaitable[T]]
    ) -> T:
        async with tenant_scope(self._factory, tenant_id) as session:
            result = await work(IdentityRepository(session))
            await session.commit()
            return result
