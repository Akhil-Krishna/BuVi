"""Section 19: RLS confines the request-path role even when an application filter is missing.

Queries here deliberately omit any `tenant_id` filter and run as `buvi_app` -- exactly
the bug RLS exists to contain.
"""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg
import pytest

from metadata_service.tests.conftest import FakeIdentity, Flows, PostgresInfo

pytestmark = [pytest.mark.integration, pytest.mark.security]

UNFILTERED = {
    "data_sources": "SELECT count(*) FROM metadata.data_sources",
    "schema_snapshots": "SELECT count(*) FROM metadata.schema_snapshots",
    "tables": "SELECT count(*) FROM metadata.tables",
    "columns": "SELECT count(*) FROM metadata.columns",
    "relationships": "SELECT count(*) FROM metadata.relationships",
}
SCOPED = {
    "data_sources": "SELECT count(*) FROM metadata.data_sources WHERE tenant_id = $1",
    "schema_snapshots": "SELECT count(*) FROM metadata.schema_snapshots WHERE tenant_id = $1",
    "tables": "SELECT count(*) FROM metadata.tables WHERE tenant_id = $1",
    "columns": (
        "SELECT count(*) FROM metadata.columns c JOIN metadata.tables t ON t.id = c.table_id "
        "WHERE t.tenant_id = $1"
    ),
    "relationships": "SELECT count(*) FROM metadata.relationships WHERE tenant_id = $1",
}


async def test_buvi_app_sees_only_the_bound_tenant(
    identity: FakeIdentity,
    flows: Flows,
    postgres: PostgresInfo,
    platform_db: Any,
    tenant: uuid.UUID,
    other_tenant: uuid.UUID,
) -> None:
    await flows.synced(identity.add(tenant_id=tenant, roles={"org_admin"}))
    theirs = await flows.synced(identity.add(tenant_id=other_tenant, roles={"org_admin"}))

    app = await asyncpg.connect(postgres.plain_app_dsn)
    try:
        for table, query in UNFILTERED.items():
            # Unbound: deny by default.
            assert await app.fetchval(query) == 0, table

        async with app.transaction():
            await app.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant))
            for table, query in UNFILTERED.items():
                expected = await platform_db.fetchval(SCOPED[table], tenant)
                assert expected > 0, table
                assert await app.fetchval(query) == expected, table

            foreign_table = uuid.UUID(theirs["table_id"])
            assert (
                await app.fetchval(
                    "SELECT count(*) FROM metadata.columns WHERE table_id = $1", foreign_table
                )
                == 0
            )

        # The setting was transaction-local: it is gone now.
        assert await app.fetchval(UNFILTERED["data_sources"]) == 0

        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with app.transaction():
                await app.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant))
                await app.execute(
                    "INSERT INTO metadata.data_sources "
                    "(tenant_id, name, engine, host_label, database_name, secret_ref, created_by) "
                    "VALUES ($1, 'x', 'postgres', 'x', 'x', 'x', $1)",
                    other_tenant,
                )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with app.transaction():
                await app.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant))
                await app.execute(
                    "INSERT INTO metadata.columns (table_id, column_name, data_type) "
                    "VALUES ($1, 'smuggled', 'text')",
                    foreign_table,
                )
    finally:
        await app.close()


async def test_deleting_a_data_source_cascades_through_relationships(
    identity: FakeIdentity, flows: Flows, platform_db: Any, tenant: uuid.UUID
) -> None:
    """ADR 0004: Section 8.2's cascade only works once relationships cascade too."""
    source = await flows.synced(identity.add(tenant_id=tenant, roles={"org_admin"}))
    source_id = uuid.UUID(source["id"])
    assert (
        await platform_db.fetchval(
            "SELECT count(*) FROM metadata.relationships WHERE tenant_id = $1", tenant
        )
        == 3
    )
    await platform_db.execute("DELETE FROM metadata.data_sources WHERE id = $1", source_id)
    for query in SCOPED.values():
        assert await platform_db.fetchval(query, tenant) == 0
