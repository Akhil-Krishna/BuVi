"""Async engine and tenant-scoped sessions (Sections 19, 20).

Section 19: "the application sets `app.tenant_id` at the start of each request-scoped
DB session". Here it is set at the start of every *transaction* instead, with
`set_config(..., is_local => true)`:

* SQLAlchemy returns the pooled connection to the pool on every commit. This service
  commits between deliveries (it must not hold a transaction open across an SMTP or webhook
  call), and the next statement may run on a different pooled connection. A session-level
  setting would silently be missing there -- and left behind on the previous connection.
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

from notification_service.core.config import Settings

#: Postgres GUC the RLS policies in migration 0001 read.
TENANT_GUC = "app.tenant_id"


#: Connection-level hardening for the platform database (ADR 0021; Phase C1 entry requirement).
#:
#: Set here, on every connection this service opens, rather than on the database server: there is
#: no Terraform yet, so a server-side-only setting would be true in dev and absent in production --
#: the exact asymmetry ADR 0014 flagged. asyncpg sends these as startup parameters, so they hold
#: for every session in every environment, including a managed database whose server config this
#: project does not own.
#:
#: `lock_timeout` is the one that closes the Phase A5 crash-resume stall: a waiter fails fast with
#: a clear error instead of blocking until its stage timeout. Alembic connects with `migration_dsn`
#: through a separate engine, so a long migration is unaffected by it.
#:
#: Duplicated per service, like `db_pool_size` and the rest of these knobs already are (Section 4
#: keeps each service's session module its own). If a third setting needs to change in lockstep,
#: extract a narrow `platform-db` package rather than letting the copies drift.
PLATFORM_SESSION_SETTINGS = {
    # Fail fast on a lock a vanished holder never released (ADR 0014, ADR 0015).
    "lock_timeout": "15s",
    # A client that died mid-transaction releases its locks within a minute.
    "idle_in_transaction_session_timeout": "60s",
    # Detect a client that vanished without a FIN in ~90s, not the Linux default 2h+.
    "tcp_keepalives_idle": "60",
    "tcp_keepalives_interval": "10",
    "tcp_keepalives_count": "3",
}


def create_engine(settings: Settings) -> AsyncEngine:
    """The process-wide engine. A request never creates its own (Section 20)."""
    return create_async_engine(
        str(settings.database_dsn),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        echo=settings.db_echo,
        connect_args={"server_settings": PLATFORM_SESSION_SETTINGS},
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
