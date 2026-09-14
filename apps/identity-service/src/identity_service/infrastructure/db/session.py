"""Async engine and request-scoped sessions (Sections 19, 20).

Two things this module is responsible for:

* **Connection pooling.** One engine per process; a request never builds its own
  engine or connection (Section 20).
* **Setting the RLS tenant.** Section 19 requires the application to set
  `app.tenant_id` at the start of each request-scoped database session, so that
  Postgres Row-Level Security catches a query whose application-layer tenant
  filter is missing. `tenant_scope` is the only place that setting is written.

The request-path role is `buvi_app`, which owns nothing and therefore cannot
bypass RLS. Alembic connects as `buvi_migrator`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from identity_service.core.config import Settings

#: Postgres GUC the RLS policies in the first migration read.
TENANT_GUC = "app.tenant_id"

#: Postgres GUC gating the SELECT-only pre-authentication lookup policies.
PRE_AUTH_GUC = "app.pre_auth_lookup"


def create_engine(settings: Settings) -> AsyncEngine:
    """Build the process-wide async engine."""
    return create_async_engine(
        str(settings.database_dsn),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        echo=settings.db_echo,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def set_tenant_scope(session: AsyncSession, tenant_id: uuid.UUID | None) -> None:
    """Bind this database session to one tenant for RLS purposes.

    Passing `None` clears the setting, which makes every RLS-protected policy
    match zero rows -- deny by default. That is the correct state for the
    pre-authentication part of the login flow.
    """
    value = str(tenant_id) if tenant_id is not None else ""
    # set_config's third argument scopes the setting to the transaction when
    # true; false makes it session-wide, which is what a pooled connection
    # needs so the setting survives each statement in the request.
    await session.execute(
        text("SELECT set_config(:guc, :value, false)"), {"guc": TENANT_GUC, "value": value}
    )


@asynccontextmanager
async def pre_auth_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Open a session that may run the three pre-authentication lookups.

    Resolving a session id, an IdP subject, or an invitation token is what
    establishes the tenant, so those reads cannot themselves be tenant-scoped.
    The matching RLS policies are declared FOR SELECT, so this scope can read
    those three tables and can never write through the exception.

    Kept as narrow as possible: the flag is set on entry, cleared on exit, and
    no caller outside `dependencies.resolve_principal` and the invitation
    acceptance path should use it.
    """
    async with session_factory() as session:
        await session.execute(text("SELECT set_config(:guc, 'on', false)"), {"guc": PRE_AUTH_GUC})
        try:
            yield session
        finally:
            await session.execute(text("SELECT set_config(:guc, '', false)"), {"guc": PRE_AUTH_GUC})
            await set_tenant_scope(session, None)


@asynccontextmanager
async def tenant_scope(
    session_factory: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID | None,
) -> AsyncIterator[AsyncSession]:
    """Open a session bound to `tenant_id`, and always clear it on the way out.

    Clearing matters because connections are pooled: a leaked `app.tenant_id`
    would silently widen the next request's RLS scope to the previous caller's
    tenant.
    """
    async with session_factory() as session:
        await set_tenant_scope(session, tenant_id)
        try:
            yield session
        finally:
            await set_tenant_scope(session, None)
