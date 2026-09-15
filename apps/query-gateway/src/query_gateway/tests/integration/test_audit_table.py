"""`query_executions` is append-only for the request path and confined by RLS (Sections 13, 19)."""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from query_gateway.tests.conftest import Api, FakeServices, PostgresInfo

pytestmark = [pytest.mark.integration, pytest.mark.security]


async def test_request_role_cannot_rewrite_or_cross_read_the_query_audit(
    api: Api,
    services: FakeServices,
    postgres: PostgresInfo,
    tenant: uuid.UUID,
    other_tenant: uuid.UUID,
) -> None:
    for owner in (tenant, other_tenant):
        who = services.add_user(owner, {"developer"})
        assert (
            await api.query(
                who, await api.data_source(owner), "SELECT id FROM sales.orders LIMIT 1"
            )
        ).status_code == 200

    app = await asyncpg.connect(postgres.plain_app_dsn)
    try:
        assert await app.fetchval("SELECT count(*) FROM query_gateway.query_executions") == 0
        async with app.transaction():
            await app.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant))
            assert (
                await app.fetchval(
                    "SELECT count(DISTINCT tenant_id) FROM query_gateway.query_executions"
                )
                == 1
            )
            assert (
                await app.fetchval("SELECT tenant_id FROM query_gateway.query_executions LIMIT 1")
                == tenant
            )
        for statement in (
            "UPDATE query_gateway.query_executions SET status = 'succeeded'",
            "DELETE FROM query_gateway.query_executions",
            "TRUNCATE query_gateway.query_executions",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with app.transaction():
                    await app.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant))
                    await app.execute(statement)
    finally:
        await app.close()
