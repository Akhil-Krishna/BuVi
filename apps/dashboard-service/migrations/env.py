"""Alembic environment for dashboard-service.

Runs as `buvi_migrator` (Section 19): the role that owns the schema and is
therefore exempt from its RLS policies. The request path never uses this role.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from dashboard_service.core.config import get_settings
from dashboard_service.infrastructure.db.models import SCHEMA, Base
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

config.set_main_option("sqlalchemy.url", str(get_settings().migration_dsn))


def _include_object(obj: object, _name: str | None, type_: str, *_args: object) -> bool:
    """Restrict autogenerate to this service's own schema."""
    if type_ == "table":
        return getattr(obj, "schema", None) == SCHEMA
    return True


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=SCHEMA,
        include_schemas=True,
        include_object=_include_object,
        compare_type=True,
        compare_server_default=True,
    )


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        version_table_schema=SCHEMA,
        include_schemas=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    # The version table lives in this service's own schema, so the schema must exist
    # before Alembic reads it (the compose init script creates it; a fresh test DB does not).
    connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
    connection.commit()
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
