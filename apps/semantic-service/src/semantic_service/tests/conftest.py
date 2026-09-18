"""semantic-service test fixtures (Section 25).

Real Postgres (migration 0001, service as the RLS-bound `buvi_app`). identity-service is faked at
its HTTP boundary; visualization-service and query-gateway are injected fakes behind the same
ports the real clients implement.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

from platform_auth import Principal, ServiceTokenIssuer, ServiceTokenVerifier
from platform_auth.permissions import permissions_for_roles
from semantic_service.core.config import Settings
from semantic_service.domain.policies.metric_expression import (
    CatalogColumnRef,
    CatalogTableRef,
)
from semantic_service.infrastructure.audit.sink import AuditRecord
from semantic_service.infrastructure.http.catalog_client import (
    CatalogColumnLookup,
    CatalogLookup,
    CatalogUnavailableError,
)

SERVICE_ROOT = Path(__file__).resolve().parents[3]
APP_ROLE_PASSWORD = "devapp"


@dataclass(frozen=True)
class PostgresInfo:
    superuser_dsn: str
    app_dsn: str

    @property
    def plain_superuser_dsn(self) -> str:
        return self.superuser_dsn.replace("postgresql+asyncpg", "postgresql")


@pytest.fixture(scope="session")
def postgres() -> Iterator[PostgresInfo]:
    import asyncpg
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16", driver="asyncpg") as container:
        dsn = container.get_connection_url()

        async def prepare() -> None:
            conn = await asyncpg.connect(dsn.replace("postgresql+asyncpg", "postgresql"))
            try:
                await conn.execute(f"CREATE ROLE buvi_app LOGIN PASSWORD '{APP_ROLE_PASSWORD}'")
            finally:
                await conn.close()

        asyncio.run(prepare())
        env = {k: v for k, v in os.environ.items() if not k.startswith("SEMANTIC_")}
        result = subprocess.run(
            ["uv", "run", "--package", "semantic-service", "alembic", "upgrade", "head"],  # noqa: S607
            cwd=SERVICE_ROOT,
            check=False,
            capture_output=True,
            text=True,
            env={**env, "SEMANTIC_MIGRATION_DSN": dsn},
        )
        if result.returncode != 0:
            raise RuntimeError(f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}")
        yield PostgresInfo(
            dsn, re.sub(r"//[^@]+@", f"//buvi_app:{APP_ROLE_PASSWORD}@", dsn, count=1)
        )


@pytest.fixture
async def platform_db(postgres: PostgresInfo) -> AsyncIterator[Any]:
    import asyncpg

    conn = await asyncpg.connect(postgres.plain_superuser_dsn)
    try:
        yield conn
    finally:
        await conn.close()


@dataclass(frozen=True)
class Caller:
    token: str
    user_id: uuid.UUID
    tenant_id: uuid.UUID

    @property
    def headers(self) -> dict[str, str]:
        return {"Cookie": f"buvi_session={self.token}"}


@dataclass
class FakeIdentity:
    principals: dict[str, dict[str, Any]] = field(default_factory=dict)

    def add_user(self, tenant_id: uuid.UUID, roles: set[str]) -> Caller:
        token = f"sess-{uuid.uuid4().hex}"
        user_id = uuid.uuid4()
        self.principals[token] = Principal(
            user_id=str(user_id),
            tenant_id=str(tenant_id),
            permissions=permissions_for_roles(frozenset(roles)),
            auth_method="session",
            mfa_verified=False,
            roles=frozenset(roles),
        ).to_dict()
        return Caller(token, user_id, tenant_id)

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/v1/oauth/token":
            form = dict(httpx.QueryParams(request.content.decode()))
            return httpx.Response(
                200,
                json={"access_token": f"svc:{form['audience']}:{form['scope']}", "expires_in": 300},
            )
        if request.url.path == "/internal/v1/introspect":
            body = json.loads(request.content)
            principal = self.principals.get(body.get("session_token") or body.get("api_key") or "")
            return (
                httpx.Response(200, json={"principal": principal})
                if principal
                else httpx.Response(401, json={"error": {"code": "AUTHENTICATION_REQUIRED"}})
            )
        return httpx.Response(404)


@pytest.fixture(scope="session")
def issuer() -> ServiceTokenIssuer:
    return ServiceTokenIssuer(issuer="identity-service", private_key_pem=None, key_id="sem-test")


@dataclass
class FakeTable:
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    visible: bool
    columns: dict[str, tuple[uuid.UUID, str, bool]]  # name -> (id, type, is_pii)


class FakeCatalog:
    """metadata-service's catalog lookup: tenant-scoped, ids only found in their own tenant."""

    def __init__(self) -> None:
        self.tables: dict[uuid.UUID, FakeTable] = {}
        self.down = False

    def add_orders(self, tenant_id: uuid.UUID, *, visible: bool = True) -> FakeTable:
        table = FakeTable(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            name="orders",
            visible=visible,
            columns={
                "id": (uuid.uuid4(), "integer", False),
                "amount": (uuid.uuid4(), "numeric(12,2)", False),
                "status": (uuid.uuid4(), "text", False),
                "order_date": (uuid.uuid4(), "date", False),
                "email": (uuid.uuid4(), "text", True),
            },
        )
        self.tables[table.id] = table
        return table

    def column_id(self, table: FakeTable, name: str) -> uuid.UUID:
        return table.columns[name][0]

    async def lookup(
        self,
        tenant_id: uuid.UUID,
        *,
        table_ids: list[uuid.UUID] | None = None,
        column_ids: list[uuid.UUID] | None = None,
    ) -> CatalogLookup:
        if self.down:
            raise CatalogUnavailableError()
        mine = {i: t for i, t in self.tables.items() if t.tenant_id == tenant_id}
        tables = {
            i: CatalogTableRef(
                table_name=t.name,
                is_visible_to_agent=t.visible,
                columns=tuple(
                    CatalogColumnRef(name=n, data_type=ty, is_pii=pii)
                    for n, (_cid, ty, pii) in t.columns.items()
                ),
            )
            for i, t in mine.items()
            if i in (table_ids or [])
        }
        columns = {
            cid: CatalogColumnLookup(
                ref=CatalogColumnRef(name=n, data_type=ty, is_pii=pii), table_id=t.id
            )
            for t in mine.values()
            for n, (cid, ty, pii) in t.columns.items()
            if cid in (column_ids or [])
        }
        return CatalogLookup(
            tables=tables,
            table_visibility={i: t.is_visible_to_agent for i, t in tables.items()},
            columns=columns,
        )


class MemoryAudit:
    def __init__(self) -> None:
        self.events: list[AuditRecord] = []

    async def record(self, event: AuditRecord) -> None:
        self.events.append(event)


@pytest.fixture
def identity() -> FakeIdentity:
    return FakeIdentity()


@pytest.fixture
def catalog() -> FakeCatalog:
    return FakeCatalog()


@pytest.fixture
def audit() -> MemoryAudit:
    return MemoryAudit()


@pytest.fixture
def tenant() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def other_tenant() -> uuid.UUID:
    return uuid.uuid4()


@dataclass
class Harness:
    client: httpx.AsyncClient
    issuer: ServiceTokenIssuer
    identity: FakeIdentity
    catalog: FakeCatalog
    audit: MemoryAudit

    def service_headers(
        self, subject: str = "analytics-orchestrator", scope: str = "semantic-service:context"
    ) -> dict[str, str]:
        token = self.issuer.issue(
            subject=subject, audience="semantic-service", scopes=frozenset({scope})
        )
        return {"X-Service-Authorization": f"Bearer {token}"}

    async def metric(
        self,
        who: Caller,
        table: FakeTable,
        *,
        name: str = "Revenue",
        expression: str = "SUM(amount)",
        **extra: Any,
    ) -> dict[str, Any]:
        response = await self.client.post(
            "/api/v1/semantic/metrics",
            json={"name": name, "expression": expression, "base_table_id": str(table.id), **extra},
            headers=who.headers,
        )
        assert response.status_code == 201, response.text
        body: dict[str, Any] = response.json()
        return body


@pytest.fixture
async def harness(
    postgres: PostgresInfo,
    issuer: ServiceTokenIssuer,
    identity: FakeIdentity,
    catalog: FakeCatalog,
    audit: MemoryAudit,
) -> AsyncIterator[Harness]:
    from semantic_service.main import create_app

    app = create_app(
        settings=Settings(
            environment="test",
            database_dsn=postgres.app_dsn,  # type: ignore[arg-type]
            migration_dsn=postgres.superuser_dsn,  # type: ignore[arg-type]
            log_level="WARNING",
        ),
        http_transport=httpx.MockTransport(identity.handler),
        service_token_verifier=ServiceTokenVerifier(
            issuer="identity-service", audience="semantic-service", keyset=issuer.jwks()
        ),
        catalog=catalog,
        audit=audit,
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://sem.test"
        ) as client,
    ):
        yield Harness(client, issuer, identity, catalog, audit)
