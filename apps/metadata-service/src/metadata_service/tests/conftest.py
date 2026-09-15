"""metadata-service test fixtures (Section 25).

One real Postgres container plays both databases the service talks to:

* the **platform database** -- migration 0001 applied, the service connected as the
  RLS-bound `buvi_app`, so a missing tenant binding fails here rather than in production;
* a **customer database** (`sample_sales`) that the Postgres connector reaches over a
  real TCP connection as a read-only role -- introspection, privilege filtering and
  driver-error sanitization all run against a real server.

identity-service is faked at its HTTP boundary (introspection, token grant, audit
events); the real one is covered by its own suite and by `scripts/test-data-sources.sh`.
Vault is the in-memory `platform_secrets` store here; the Vault adapter is unit tested in
platform-secrets and exercised live by the scripted flow.
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

from metadata_service.core.config import Settings
from platform_auth import Principal, ServiceTokenIssuer, ServiceTokenVerifier
from platform_auth.permissions import permissions_for_roles
from platform_observability.logging import JsonFormatter
from platform_secrets import InMemorySecretStore, SecretStore

SERVICE_ROOT = Path(__file__).resolve().parents[3]

APP_ROLE_PASSWORD = "devapp"
READER_USER = "sales_reader"
READER_PASSWORD = "Reader-Pa55word-7d41c2"
CUSTOMER_DB = "sample_sales"
SESSION_COOKIE = "buvi_session"

#: The customer schema the catalog tests assert against. `hidden_costs` has no SELECT
#: grant and `private` is outside `allowed_schemas`: neither may appear in the catalog.
CUSTOMER_SCHEMA_SQL = """
CREATE SCHEMA sales;
CREATE SCHEMA private;
CREATE TABLE sales.regions (id integer PRIMARY KEY, name text NOT NULL);
COMMENT ON TABLE sales.regions IS 'Sales regions';
CREATE TABLE sales.customers (
    id integer PRIMARY KEY,
    name text NOT NULL,
    email text NOT NULL,
    region_id integer NOT NULL REFERENCES sales.regions (id)
);
CREATE TABLE sales.orders (
    id bigint PRIMARY KEY,
    customer_id integer NOT NULL REFERENCES sales.customers (id),
    order_date date NOT NULL,
    amount numeric(12, 2) NOT NULL
);
COMMENT ON COLUMN sales.orders.amount IS 'Order total in USD';
CREATE TABLE sales.order_items (
    order_id bigint NOT NULL REFERENCES sales.orders (id),
    line_no integer NOT NULL,
    product text NOT NULL,
    quantity integer NOT NULL,
    PRIMARY KEY (order_id, line_no)
);
CREATE VIEW sales.monthly_revenue AS
    SELECT date_trunc('month', order_date) AS month, sum(amount) AS revenue
    FROM sales.orders GROUP BY 1;
CREATE TABLE sales.hidden_costs (id integer PRIMARY KEY, cost numeric);
CREATE TABLE private.salaries (id integer PRIMARY KEY, amount numeric);
INSERT INTO sales.regions VALUES (1, 'North America'), (2, 'Europe');
ANALYZE;
GRANT USAGE ON SCHEMA sales, private TO sales_reader;
GRANT SELECT ON sales.regions, sales.customers, sales.orders, sales.order_items,
    sales.monthly_revenue, private.salaries TO sales_reader;
"""

EXPECTED_TABLES = ["customers", "monthly_revenue", "order_items", "orders", "regions"]


# --- Postgres ----------------------------------------------------------------------------


@dataclass(frozen=True)
class PostgresInfo:
    #: SQLAlchemy URL for the platform database as the container superuser.
    superuser_dsn: str
    #: SQLAlchemy URL for the platform database as `buvi_app`.
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


def _clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("METADATA_")}


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
                await customer.execute(CUSTOMER_SCHEMA_SQL)
            finally:
                await customer.close()

        asyncio.run(prepare())
        result = subprocess.run(
            ["uv", "run", "--package", "metadata-service", "alembic", "upgrade", "head"],  # noqa: S607
            cwd=SERVICE_ROOT,
            check=False,
            capture_output=True,
            text=True,
            env={**_clean_env(), "METADATA_MIGRATION_DSN": dsn},
        )
        if result.returncode != 0:
            raise RuntimeError(f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}")
        yield info


@pytest.fixture
async def platform_db(postgres: PostgresInfo) -> AsyncIterator[Any]:
    """A superuser connection to the platform database, for arranging and inspecting rows."""
    import asyncpg

    conn = await asyncpg.connect(postgres.plain_superuser_dsn)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def customer_db(postgres: PostgresInfo) -> AsyncIterator[Any]:
    """A superuser connection to the customer database, for schema-change tests."""
    import asyncpg

    conn = await asyncpg.connect(postgres.customer_dsn())
    try:
        yield conn
    finally:
        await conn.close()


# --- identity-service at its HTTP boundary ------------------------------------------------


@dataclass(frozen=True)
class Caller:
    token: str
    user_id: uuid.UUID
    tenant_id: uuid.UUID

    @property
    def headers(self) -> dict[str, str]:
        return {"Cookie": f"{SESSION_COOKIE}={self.token}"}


@dataclass
class FakeIdentity:
    principals: dict[str, dict[str, Any]] = field(default_factory=dict)
    audit_events: list[dict[str, Any]] = field(default_factory=list)
    audit_headers: list[dict[str, str]] = field(default_factory=list)
    audit_status: int = 204
    introspect_status: int | None = None
    introspections: int = 0

    def add(
        self,
        *,
        tenant_id: uuid.UUID,
        roles: set[str] | frozenset[str],
        mfa_age: dt.timedelta | None = dt.timedelta(0),
    ) -> Caller:
        token = f"sess-{uuid.uuid4().hex}"
        user_id = uuid.uuid4()
        verified_at = dt.datetime.now(dt.UTC) - mfa_age if mfa_age is not None else None
        self.principals[token] = Principal(
            user_id=str(user_id),
            tenant_id=str(tenant_id),
            permissions=permissions_for_roles(frozenset(roles)),
            auth_method="session",
            mfa_verified=verified_at is not None,
            session_id=f"s-{token}",
            mfa_verified_at=verified_at,
            roles=frozenset(roles),
        ).to_dict()
        return Caller(token=token, user_id=user_id, tenant_id=tenant_id)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/internal/v1/oauth/token":
            form = dict(httpx.QueryParams(request.content.decode()))
            return httpx.Response(
                200,
                json={
                    "access_token": f"svc:{form['audience']}:{form.get('scope', '')}",
                    "token_type": "Bearer",
                    "expires_in": 300,
                    "scope": form.get("scope", ""),
                },
            )
        if path == "/internal/v1/introspect":
            self.introspections += 1
            if self.introspect_status is not None:
                return httpx.Response(
                    self.introspect_status, json={"error": {"code": "INTERNAL_ERROR"}}
                )
            body = json.loads(request.content)
            credential = body.get("session_token") or body.get("api_key")
            if credential == "inactive":
                return httpx.Response(403, json={"error": {"code": "USER_NOT_ACTIVE"}})
            if credential in self.principals:
                return httpx.Response(200, json={"principal": self.principals[credential]})
            return httpx.Response(401, json={"error": {"code": "AUTHENTICATION_REQUIRED"}})
        if path == "/internal/v1/audit-events":
            if self.audit_status == 204:
                self.audit_events.append(json.loads(request.content))
                self.audit_headers.append(dict(request.headers))
            return httpx.Response(self.audit_status)
        if path == "/health/ready":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(404)


@pytest.fixture
def identity() -> FakeIdentity:
    return FakeIdentity()


@pytest.fixture(scope="session")
def gateway_issuer() -> ServiceTokenIssuer:
    """Stands in for identity-service's signing key when a test sends a gateway token."""
    return ServiceTokenIssuer(issuer="identity-service", private_key_pem=None, key_id="test-1")


@pytest.fixture
def secrets() -> InMemorySecretStore:
    return InMemorySecretStore()


@pytest.fixture
def tenant() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def other_tenant() -> uuid.UUID:
    return uuid.uuid4()


# --- The application ---------------------------------------------------------------------


def make_settings(postgres: PostgresInfo, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "database_dsn": postgres.app_dsn,
        "migration_dsn": postgres.superuser_dsn,
        "vault_use_memory_stub": True,
        "connector_allowed_internal_hosts": [postgres.host],
        "connector_connect_timeout_seconds": 5.0,
        "log_level": "WARNING",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def settings(postgres: PostgresInfo) -> Settings:
    return make_settings(postgres)


@asynccontextmanager
async def running_app(
    settings: Settings,
    identity: FakeIdentity,
    secrets: SecretStore,
    issuer: ServiceTokenIssuer,
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    from metadata_service.main import create_app

    app = create_app(
        settings=settings,
        secrets=secrets,
        http_transport=httpx.MockTransport(identity.handler),
        service_token_verifier=ServiceTokenVerifier(
            issuer="identity-service", audience="metadata-service", keyset=issuer.jwks()
        ),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://metadata.test"
        ) as client,
    ):
        yield app, client


@pytest.fixture
async def client(
    settings: Settings,
    identity: FakeIdentity,
    secrets: InMemorySecretStore,
    gateway_issuer: ServiceTokenIssuer,
) -> AsyncIterator[httpx.AsyncClient]:
    async with running_app(settings, identity, secrets, gateway_issuer) as (_, http_client):
        yield http_client


# --- Flows over HTTP ----------------------------------------------------------------------


class Flows:
    """The API calls most tests start from. Everything goes over HTTP."""

    def __init__(self, client: httpx.AsyncClient, postgres: PostgresInfo) -> None:
        self.client = client
        self.postgres = postgres

    @staticmethod
    def create_body(**overrides: Any) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": "sample-sales-db",
            "engine": "postgres",
            "host_label": "sample-sales-db (test)",
            "database_name": CUSTOMER_DB,
            "allowed_schemas": ["sales"],
        }
        body.update(overrides)
        return body

    def secret_body(self, **overrides: Any) -> dict[str, Any]:
        body: dict[str, Any] = {
            "host": self.postgres.host,
            "port": self.postgres.port,
            "username": READER_USER,
            "password": READER_PASSWORD,
            "sslmode": "disable",
        }
        body.update(overrides)
        return body

    async def create(self, who: Caller, **overrides: Any) -> dict[str, Any]:
        response = await self.client.post(
            "/api/v1/data-sources", json=self.create_body(**overrides), headers=who.headers
        )
        assert response.status_code == 201, response.text
        created: dict[str, Any] = response.json()
        return created

    async def set_secret(
        self, who: Caller, data_source_id: str, **overrides: Any
    ) -> httpx.Response:
        return await self.client.post(
            f"/api/v1/data-sources/{data_source_id}/secret",
            json=self.secret_body(**overrides),
            headers=who.headers,
        )

    async def synced(self, who: Caller, **overrides: Any) -> dict[str, Any]:
        """Create, set credentials and sync a data source; returns it with a table id."""
        created = await self.create(who, **overrides)
        secret = await self.set_secret(who, created["id"])
        assert secret.status_code == 200, secret.text
        sync = await self.client.post(
            f"/api/v1/data-sources/{created['id']}/sync", headers=who.headers
        )
        assert sync.status_code == 200 and sync.json()["ok"], sync.text
        tables = await self.client.get(
            f"/api/v1/data-sources/{created['id']}/tables", headers=who.headers
        )
        created["table_id"] = tables.json()["items"][0]["id"]
        return created


@pytest.fixture
def flows(client: httpx.AsyncClient, postgres: PostgresInfo) -> Flows:
    return Flows(client, postgres)


@pytest.fixture
def captured_logs(client: httpx.AsyncClient) -> Iterator[list[str]]:
    """Every log record at every level, formatted and raw, while the test runs.

    Depends on `client` so it is installed after `create_app` reconfigures logging.
    """
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
