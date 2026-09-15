"""query-gateway test fixtures (Section 25).

One Postgres container is both the platform database (migration 0001, service running as the
RLS-bound `buvi_app`) and a customer database (`sample_sales`) reached over real TCP as the
read-only `sales_reader` role -- so the database-side guarantees (read-only transaction, grants,
prepared single statement, statement timeout) are exercised for real, not mocked.

identity-service and metadata-service are faked at their HTTP boundaries; Vault and the result
store are the in-memory implementations (MinIO has its own container test).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import re
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from platform_auth import Principal, ServiceTokenIssuer, ServiceTokenVerifier
from platform_auth.permissions import permissions_for_roles
from platform_observability.logging import JsonFormatter
from platform_secrets import InMemorySecretStore, SecretStore
from query_gateway.core.config import Settings
from query_gateway.infrastructure.storage.base import InMemoryResultStore, ResultStore

SERVICE_ROOT = Path(__file__).resolve().parents[3]
APP_ROLE_PASSWORD = "devapp"
READER_USER = "sales_reader"
READER_PASSWORD = "Qg-Reader-Pa55word-" + uuid.uuid4().hex[:8]
CUSTOMER_DB = "sample_sales"
SESSION_COOKIE = "buvi_session"

CUSTOMER_SQL = """
CREATE SCHEMA sales;
CREATE TABLE sales.regions (id integer PRIMARY KEY, name text NOT NULL);
CREATE TABLE sales.customers (
    id integer PRIMARY KEY, name text NOT NULL, email text NOT NULL,
    region_id integer NOT NULL REFERENCES sales.regions (id)
);
CREATE TABLE sales.orders (
    id integer PRIMARY KEY, customer_id integer NOT NULL REFERENCES sales.customers (id),
    order_date date NOT NULL, status text NOT NULL, amount numeric(12, 2) NOT NULL
);
CREATE TABLE sales.scratch (id integer);
INSERT INTO sales.regions VALUES (1, 'North America'), (2, 'Europe');
INSERT INTO sales.customers
    SELECT g, 'Customer ' || g, 'customer' || g || '@example.com', 1 + g % 2
    FROM generate_series(1, 50) AS g;
INSERT INTO sales.orders
    SELECT g, 1 + g % 50, DATE '2026-04-01' + (g % 90),
           (ARRAY['completed', 'refunded'])[1 + g % 2], (g % 100) + 0.5
    FROM generate_series(1, 2000) AS g;
GRANT USAGE ON SCHEMA sales TO sales_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA sales TO sales_reader;
"""


@dataclass(frozen=True)
class PostgresInfo:
    superuser_dsn: str
    app_dsn: str
    host: str
    port: int

    @property
    def plain_superuser_dsn(self) -> str:
        return self.superuser_dsn.replace("postgresql+asyncpg", "postgresql")

    @property
    def plain_app_dsn(self) -> str:
        return self.app_dsn.replace("postgresql+asyncpg", "postgresql")

    def customer_dsn(self) -> str:
        return self.plain_superuser_dsn.rsplit("/", 1)[0] + f"/{CUSTOMER_DB}"


@pytest.fixture(scope="session")
def postgres() -> Iterator[PostgresInfo]:
    import asyncpg
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16", driver="asyncpg") as container:
        dsn = container.get_connection_url()
        info = PostgresInfo(
            superuser_dsn=dsn,
            app_dsn=re.sub(r"//[^@]+@", f"//buvi_app:{APP_ROLE_PASSWORD}@", dsn, count=1),
            host=container.get_container_host_ip(),
            port=int(container.get_exposed_port(5432)),
        )

        async def prepare() -> None:
            conn = await asyncpg.connect(info.plain_superuser_dsn)
            try:
                await conn.execute(f"CREATE ROLE buvi_app LOGIN PASSWORD '{APP_ROLE_PASSWORD}'")
                await conn.execute(f"CREATE ROLE {READER_USER} LOGIN PASSWORD '{READER_PASSWORD}'")
                await conn.execute(f"CREATE DATABASE {CUSTOMER_DB}")
            finally:
                await conn.close()
            customer = await asyncpg.connect(info.customer_dsn())
            try:
                await customer.execute(CUSTOMER_SQL)
            finally:
                await customer.close()

        asyncio.run(prepare())
        env = {k: v for k, v in os.environ.items() if not k.startswith("QUERY_GATEWAY_")}
        result = subprocess.run(
            ["uv", "run", "--package", "query-gateway", "alembic", "upgrade", "head"],  # noqa: S607
            cwd=SERVICE_ROOT,
            check=False,
            capture_output=True,
            text=True,
            env={**env, "QUERY_GATEWAY_MIGRATION_DSN": dsn},
        )
        if result.returncode != 0:
            raise RuntimeError(f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}")
        yield info


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


def table(schema: str, name: str, *columns: str, pii: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "schema_name": schema,
        "table_name": name,
        "is_visible_to_agent": True,
        "columns": [{"column_name": c, "data_type": "text", "is_pii": c in pii} for c in columns],
    }


CATALOG = [
    table("sales", "orders", "id", "customer_id", "order_date", "status", "amount"),
    table("sales", "customers", "id", "name", "email", "region_id", pii=("email",)),
    table("sales", "regions", "id", "name"),
    table("sales", "scratch", "id"),
]


@dataclass
class FakeServices:
    """identity-service and metadata-service at their HTTP boundaries."""

    principals: dict[str, dict[str, Any]] = field(default_factory=dict)
    policies: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    policy_requests: list[httpx.Request] = field(default_factory=list)

    def add_user(self, tenant_id: uuid.UUID, roles: set[str]) -> Caller:
        token = f"sess-{uuid.uuid4().hex}"
        user_id = uuid.uuid4()
        self.principals[token] = Principal(
            user_id=str(user_id),
            tenant_id=str(tenant_id),
            permissions=permissions_for_roles(frozenset(roles)),
            auth_method="session",
            mfa_verified=True,
            session_id=f"s-{token}",
            mfa_verified_at=dt.datetime.now(dt.UTC),
            roles=frozenset(roles),
        ).to_dict()
        return Caller(token, user_id, tenant_id)

    def add_data_source(
        self, tenant_id: uuid.UUID, *, status: str = "active", secret_ref: str | None = None
    ) -> uuid.UUID:
        data_source_id = uuid.uuid4()
        self.policies[(str(tenant_id), str(data_source_id))] = {
            "data_source_id": str(data_source_id),
            "tenant_id": str(tenant_id),
            "engine": "postgres",
            "database_name": CUSTOMER_DB,
            "allowed_schemas": ["sales"],
            "status": status,
            "secret_ref": secret_ref
            or f"secret/data/tenants/{tenant_id}/datasources/{data_source_id}",
            "last_sync_at": None,
            "tables": CATALOG,
        }
        return data_source_id

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/internal/v1/oauth/token":
            form = dict(httpx.QueryParams(request.content.decode()))
            return httpx.Response(
                200,
                json={
                    "access_token": f"svc:{form['audience']}:{form.get('scope', '')}",
                    "expires_in": 300,
                },
            )
        if path == "/internal/v1/introspect":
            body = json.loads(request.content)
            credential = body.get("session_token") or body.get("api_key")
            if credential in self.principals:
                return httpx.Response(200, json={"principal": self.principals[credential]})
            return httpx.Response(401, json={"error": {"code": "AUTHENTICATION_REQUIRED"}})
        match = re.fullmatch(r"/internal/v1/data-sources/([^/]+)/query-policy", path)
        if match:
            self.policy_requests.append(request)
            policy = self.policies.get((request.url.params.get("tenant_id", ""), match.group(1)))
            if policy is None:
                return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})
            return httpx.Response(200, json=policy)
        if path in ("/health/ready", "/health/live"):
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(404)


@pytest.fixture
def services() -> FakeServices:
    return FakeServices()


@pytest.fixture(scope="session")
def issuer() -> ServiceTokenIssuer:
    return ServiceTokenIssuer(issuer="identity-service", private_key_pem=None, key_id="qg-test")


@pytest.fixture
def secrets() -> InMemorySecretStore:
    return InMemorySecretStore()


@pytest.fixture
def results() -> InMemoryResultStore:
    return InMemoryResultStore()


@pytest.fixture
def tenant() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def other_tenant() -> uuid.UUID:
    return uuid.uuid4()


def make_settings(postgres: PostgresInfo, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "database_dsn": postgres.app_dsn,
        "migration_dsn": postgres.superuser_dsn,
        "vault_use_memory_stub": True,
        "result_store_use_memory_stub": True,
        "connector_allowed_internal_hosts": [postgres.host],
        "policy_cache_ttl_seconds": 0,
        "log_level": "WARNING",
    }
    values.update(overrides)
    return Settings(**values)


def reader_secret(postgres: PostgresInfo, **overrides: str) -> dict[str, str]:
    payload = {
        "host": postgres.host,
        "port": str(postgres.port),
        "username": READER_USER,
        "password": READER_PASSWORD,
        "sslmode": "disable",
    }
    payload.update(overrides)
    return payload


@asynccontextmanager
async def running_app(
    settings: Settings,
    services: FakeServices,
    secrets: SecretStore,
    results: ResultStore,
    issuer: ServiceTokenIssuer,
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    from query_gateway.main import create_app

    app = create_app(
        settings=settings,
        secrets=secrets,
        results=results,
        http_transport=httpx.MockTransport(services.handler),
        service_token_verifier=ServiceTokenVerifier(
            issuer="identity-service", audience="query-gateway", keyset=issuer.jwks()
        ),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://qg.test"
        ) as client,
    ):
        yield app, client


@pytest.fixture
async def app_client(
    postgres: PostgresInfo,
    services: FakeServices,
    secrets: InMemorySecretStore,
    results: InMemoryResultStore,
    issuer: ServiceTokenIssuer,
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    async with running_app(make_settings(postgres), services, secrets, results, issuer) as pair:
        yield pair


@pytest.fixture
def client(app_client: tuple[FastAPI, httpx.AsyncClient]) -> httpx.AsyncClient:
    return app_client[1]


class Api:
    """Builds a caller's request: a service token (the calling service) and a user credential."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        issuer: ServiceTokenIssuer,
        services: FakeServices,
        secrets: InMemorySecretStore,
        postgres: PostgresInfo,
    ) -> None:
        self.client = client
        self.issuer = issuer
        self.services = services
        self.secrets = secrets
        self.postgres = postgres

    def service_header(
        self,
        subject: str = "api-gateway",
        scope: str = "query-gateway:execute",
        audience: str = "query-gateway",
    ) -> dict[str, str]:
        token = self.issuer.issue(subject=subject, audience=audience, scopes=frozenset({scope}))
        return {"X-Service-Authorization": f"Bearer {token}"}

    async def data_source(self, tenant_id: uuid.UUID, **secret_overrides: str) -> uuid.UUID:
        data_source_id = self.services.add_data_source(tenant_id)
        await self.secrets.write(
            f"tenants/{tenant_id}/datasources/{data_source_id}",
            reader_secret(self.postgres, **secret_overrides),
        )
        return data_source_id

    async def query(
        self,
        who: Caller | None,
        data_source_id: uuid.UUID,
        sql: str,
        *,
        purpose: str = "sql_editor",
        service: dict[str, str] | None = None,
        **extra: Any,
    ) -> httpx.Response:
        headers = dict(self.service_header() if service is None else service)
        if who is not None:
            headers["Cookie"] = f"{SESSION_COOKIE}={who.token}"
        body = {"database_id": str(data_source_id), "sql": sql, "purpose": purpose, **extra}
        return await self.client.post("/internal/v1/queries", json=body, headers=headers)


@pytest.fixture
def api(
    client: httpx.AsyncClient,
    issuer: ServiceTokenIssuer,
    services: FakeServices,
    secrets: InMemorySecretStore,
    postgres: PostgresInfo,
) -> Api:
    return Api(client, issuer, services, secrets, postgres)


@pytest.fixture
def captured_logs(client: httpx.AsyncClient) -> Iterator[list[str]]:
    lines: list[str] = []

    class Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(JsonFormatter("test").format(record))
            lines.append(repr(record.__dict__))

    root = logging.getLogger()
    handler = Collector(level=logging.DEBUG)
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield lines
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)


async def audit_rows(platform_db: Any, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await platform_db.fetch(
        "SELECT * FROM query_gateway.query_executions WHERE tenant_id = $1 ORDER BY created_at",
        tenant_id,
    )
    return [dict(r) for r in rows]
