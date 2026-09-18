"""mcp-gateway test fixtures (Section 25).

* Real Postgres: migration 0001, with the service running as the RLS-bound `buvi_app`.
* identity-service is faked at its HTTP boundary.
* The MCP servers are **real**: the official SDK's FastMCP over Streamable HTTP, served with TLS
  from a test CA, in both response modes.

Names resolve through a fake resolver to public-looking addresses. `LoopbackTransport` then
delivers packets for those addresses to the local servers, so the production path runs
unchanged: resolution, the egress check, pinning, SNI and certificate verification against the
hostname.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import ipaddress
import json
import os
import re
import ssl
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

from mcp_gateway.core.config import Settings
from mcp_gateway.infrastructure.audit.sink import AuditRecord
from platform_auth import Principal, ServiceTokenIssuer, ServiceTokenVerifier
from platform_auth.permissions import permissions_for_roles
from platform_contracts import McpInvocationDenied
from platform_egress import HostResolutionError, IPAddress
from platform_secrets import InMemorySecretStore
from platform_testing.mcp import TlsFiles, make_tls, sample_mcp_app, serve

SERVICE_ROOT = Path(__file__).resolve().parents[3]
APP_ROLE_PASSWORD = "devapp"

#: Hostnames the tests register, and the public addresses they "resolve" to.
JSON_HOST = "mcp.example.com"
SSE_HOST = "sse.example.com"
JSON_ADDRESS = "93.184.215.14"
SSE_ADDRESS = "93.184.215.15"


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
        env = {k: v for k, v in os.environ.items() if not k.startswith("MCP_")}
        result = subprocess.run(
            ["uv", "run", "--package", "mcp-gateway", "alembic", "upgrade", "head"],  # noqa: S607
            cwd=SERVICE_ROOT,
            check=False,
            capture_output=True,
            text=True,
            env={**env, "MCP_MIGRATION_DSN": dsn},
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


# --- Real MCP servers ----------------------------------------------------------------------


@dataclass
class McpServers:
    tls: TlsFiles
    json_port: int
    sse_port: int
    calls: list[str]


@pytest.fixture(scope="session")
def mcp_servers(tmp_path_factory: pytest.TempPathFactory) -> Iterator[McpServers]:
    tls = make_tls(tmp_path_factory.mktemp("tls"), [JSON_HOST, SSE_HOST])
    calls: list[str] = []
    json_app = sample_mcp_app(json_response=True, allowed_hosts=[JSON_HOST], calls=calls)
    sse_app = sample_mcp_app(json_response=False, allowed_hosts=[SSE_HOST], calls=calls)
    with serve(json_app, tls=tls) as json_port, serve(sse_app, tls=tls) as sse_port:
        yield McpServers(tls, json_port, sse_port, calls)


class LoopbackTransport(httpx.AsyncBaseTransport):
    """Delivers requests for the fake public addresses to the local servers, verifying TLS
    against the test CA, and records every destination the gateway actually tried."""

    def __init__(self, routes: dict[tuple[str, int], int], ca: Path) -> None:
        self.routes = routes
        self.attempts: list[str] = []
        #: (destination, Authorization header) of every request, to prove where a token goes.
        self.authorizations: list[tuple[str, str | None]] = []
        context = ssl.create_default_context(cafile=str(ca))
        self._inner = httpx.AsyncHTTPTransport(
            verify=context, limits=httpx.Limits(max_keepalive_connections=0), retries=0
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        port = request.url.port or (443 if request.url.scheme == "https" else 80)
        self.attempts.append(f"{host}:{port}")
        self.authorizations.append(
            (request.headers.get("host", ""), request.headers.get("authorization"))
        )
        local = self.routes.get((host, port))
        if local is None:
            raise httpx.ConnectError("no route to host (test network)", request=request)
        request.url = request.url.copy_with(host="127.0.0.1", port=local)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


@dataclass
class FakeDns:
    """Hostname -> addresses, changeable mid-test (DNS rebinding)."""

    records: dict[str, list[str]] = field(default_factory=dict)

    async def resolve(self, host: str, _port: int) -> list[IPAddress]:
        try:
            return [ipaddress.ip_address(host)]
        except ValueError:
            pass
        if host not in self.records:
            raise HostResolutionError()
        return [ipaddress.ip_address(a) for a in self.records[host]]


# --- Identity, audit, events ---------------------------------------------------------------


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

    def add_user(self, tenant_id: uuid.UUID, roles: set[str], *, fresh_mfa: bool = False) -> Caller:
        token = f"sess-{uuid.uuid4().hex}"
        user_id = uuid.uuid4()
        self.principals[token] = Principal(
            user_id=str(user_id),
            tenant_id=str(tenant_id),
            permissions=permissions_for_roles(frozenset(roles)),
            auth_method="session",
            mfa_verified=fresh_mfa,
            mfa_verified_at=dt.datetime.now(dt.UTC) if fresh_mfa else None,
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


class MemoryAudit:
    def __init__(self) -> None:
        self.events: list[AuditRecord] = []

    async def record(self, event: AuditRecord) -> None:
        self.events.append(event)

    def types(self) -> list[str]:
        return [e.event_type for e in self.events]


class MemoryEvents:
    def __init__(self) -> None:
        self.denied: list[McpInvocationDenied] = []

    async def invocation_denied(self, event: McpInvocationDenied) -> None:
        self.denied.append(event)


@pytest.fixture(scope="session")
def issuer() -> ServiceTokenIssuer:
    return ServiceTokenIssuer(issuer="identity-service", private_key_pem=None, key_id="mcp-test")


@pytest.fixture
def tenant() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def other_tenant() -> uuid.UUID:
    return uuid.uuid4()


@dataclass
class Harness:
    client: httpx.AsyncClient
    identity: FakeIdentity
    audit: MemoryAudit
    events: MemoryEvents
    secrets: InMemorySecretStore
    dns: FakeDns
    network: LoopbackTransport
    servers: McpServers

    async def register(
        self,
        who: Caller,
        *,
        host: str = JSON_HOST,
        path: str = "/mcp",
        tools: dict[str, str] | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        declared = tools or {"search_docs": "read_metadata", "lookup_order": "read_data"}
        response = await self.client.post(
            "/api/v1/mcp/servers",
            json={
                "name": f"Docs {uuid.uuid4().hex[:6]}",
                "endpoint_url": f"https://{host}{path}",
                "tools": [{"name": n, "tool_class": c} for n, c in declared.items()],
                **extra,
            },
            headers=who.headers,
        )
        assert response.status_code == 201, response.text
        body: dict[str, Any] = response.json()
        return body

    async def approve(self, admin: Caller, server_id: str) -> httpx.Response:
        return await self.client.post(
            f"/api/v1/mcp/servers/{server_id}/approve", headers=admin.headers
        )

    async def grant(
        self, admin: Caller, server_id: str, tool: str, **grantee: Any
    ) -> httpx.Response:
        return await self.client.post(
            f"/api/v1/mcp/servers/{server_id}/tools/{tool}/grants",
            json={k: str(v) if isinstance(v, uuid.UUID) else v for k, v in grantee.items()},
            headers=admin.headers,
        )

    async def invoke(
        self, who: Caller, server_id: str, tool: str, arguments: dict[str, Any] | None = None
    ) -> httpx.Response:
        return await self.client.post(
            f"/api/v1/mcp/servers/{server_id}/tools/{tool}/invoke",
            json={"arguments": arguments or {}},
            headers=who.headers,
        )


def app_settings(postgres: PostgresInfo, **overrides: Any) -> Settings:
    return Settings(
        environment="test",
        database_dsn=postgres.app_dsn,  # type: ignore[arg-type]
        migration_dsn=postgres.superuser_dsn,  # type: ignore[arg-type]
        log_level="WARNING",
        **overrides,
    )


@pytest.fixture
async def harness(
    postgres: PostgresInfo, issuer: ServiceTokenIssuer, mcp_servers: McpServers
) -> AsyncIterator[Harness]:
    from mcp_gateway.main import create_app

    identity, audit, events, secrets = (
        FakeIdentity(),
        MemoryAudit(),
        MemoryEvents(),
        InMemorySecretStore(),
    )
    dns = FakeDns({JSON_HOST: [JSON_ADDRESS], SSE_HOST: [SSE_ADDRESS]})
    network = LoopbackTransport(
        {(JSON_ADDRESS, 443): mcp_servers.json_port, (SSE_ADDRESS, 443): mcp_servers.sse_port},
        mcp_servers.tls.ca,
    )
    app = create_app(
        settings=app_settings(postgres, max_response_bytes=200_000),
        http_transport=httpx.MockTransport(identity.handler),
        service_token_verifier=ServiceTokenVerifier(
            issuer="identity-service", audience="mcp-gateway", keyset=issuer.jwks()
        ),
        secrets=secrets,
        audit=audit,
        events=events,
        mcp_transport=network,
        resolver=dns.resolve,
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://mcp-gw.test"
        ) as client,
    ):
        yield Harness(client, identity, audit, events, secrets, dns, network, mcp_servers)
