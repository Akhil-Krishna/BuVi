"""Async engine and tenant-scoped sessions (Sections 19, 20).

Section 19: "the application sets `app.tenant_id` at the start of each request-scoped
DB session". Here it is set at the start of every *transaction* instead, with
`set_config(..., is_local => true)`:

* SQLAlchemy returns the pooled connection to the pool on every commit. The data-source
  service commits mid-request (it must not hold a transaction open across a slow
  customer-database round trip), and the next statement may run on a different pooled
  connection. A session-level setting would silently be missing there -- and left
  behind on the previous connection.
* A transaction-local setting cannot leak: Postgres discards it when the transaction
  ends, whichever request the connection serves next.

The request-path role is `buvi_app`, which owns nothing and so cannot bypass RLS.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, SessionTransaction

from dashboard_service.core.config import Settings

#: Postgres GUC the RLS policies in migration 0001 read.
TENANT_GUC = "app.tenant_id"


def create_engine(settings: Settings) -> AsyncEngine:
    """The process-wide engine. A request never creates its own (Section 20)."""
    return create_async_engine(
        str(settings.database_dsn),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        echo=settings.db_echo,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def tenant_scope(
    session_factory: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> AsyncIterator[AsyncSession]:
    """A session whose every transaction is bound to `tenant_id` for RLS."""
    value = str(tenant_id)

    async with session_factory() as session:

        def bind_tenant(
            _session: Session, _transaction: SessionTransaction, connection: Connection
        ) -> None:
            connection.execute(
                text("SELECT set_config(:guc, :value, true)"), {"guc": TENANT_GUC, "value": value}
            )

        event.listen(session.sync_session, "after_begin", bind_tenant)
        try:
            yield session
        finally:
            event.remove(session.sync_session, "after_begin", bind_tenant)


#: Enables the FOR SELECT policy on `share_links` (migration 0002) for one transaction.
SHARE_LOOKUP_GUC = "app.share_lookup"


@asynccontextmanager
async def share_lookup_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """A session that may look a share link up by its token hash before any tenant is known.

    The setting is transaction-local and bound to no tenant, so nothing but `share_links`
    rows are readable, and nothing is writable. Use it for the one keyed lookup only.
    """
    async with session_factory() as session:

        def enable(
            _session: Session, _transaction: SessionTransaction, connection: Connection
        ) -> None:
            connection.execute(
                text("SELECT set_config(:guc, 'on', true)"), {"guc": SHARE_LOOKUP_GUC}
            )

        event.listen(session.sync_session, "after_begin", enable)
        try:
            yield session
        finally:
            event.remove(session.sync_session, "after_begin", enable)
