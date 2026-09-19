"""notification-service test fixtures (Section 25).

* Real Postgres: migration 0001, with the service running as the RLS-bound `buvi_app`.
* identity-service is faked at its HTTP boundary (introspection and the directory), so the real
  directory client runs.
* Webhook receivers are faked at the wire: `Receivers` answers requests for the fake public
  addresses a `FakeDns` hands out, so resolution, the egress check and pinning run unchanged.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import ipaddress
import json
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

from notification_service.core.config import Settings
from notification_service.infrastructure.audit.sink import AuditRecord
from notification_service.infrastructure.email.smtp import InMemoryEmailSender
from platform_auth import Principal, ServiceTokenIssuer, ServiceTokenVerifier
from platform_auth.permissions import permissions_for_roles
from platform_egress import HostResolutionError, IPAddress
from platform_secrets import InMemorySecretStore

SERVICE_ROOT = Path(__file__).resolve().parents[3]
APP_ROLE_PASSWORD = "devapp"

HOOK_HOST = "hooks.example.com"
HOOK_ADDRESS = "93.184.215.20"


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
        env = {k: v for k, v in os.environ.items() if not k.startswith("NOTIFICATION_")}
        result = subprocess.run(
            ["uv", "run", "--package", "notification-service", "alembic", "upgrade", "head"],  # noqa: S607
            cwd=SERVICE_ROOT,
            check=False,
            capture_output=True,
            text=True,
            env={**env, "NOTIFICATION_MIGRATION_DSN": dsn},
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


# --- identity-service ------------------------------------------------------------------------


@dataclass(frozen=True)
class Caller:
    token: str
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Cookie": f"buvi_session={self.token}"}


@dataclass
class FakeIdentity:
    principals: dict[str, dict[str, Any]] = field(default_factory=dict)
    inactive: set[str] = field(default_factory=set)
    directory_down: bool = False
    directory_calls: list[dict[str, Any]] = field(default_factory=list)

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
            mfa_method="totp" if fresh_mfa else None,
            roles=frozenset(roles),
        ).to_dict()
        return Caller(token, user_id, tenant_id, f"u-{user_id.hex[:8]}@example.com")

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/internal/v1/oauth/token":
            form = dict(httpx.QueryParams(request.content.decode()))
            return httpx.Response(
                200,
                json={"access_token": f"svc:{form['audience']}:{form['scope']}", "expires_in": 300},
            )
        if path == "/internal/v1/introspect":
            body = json.loads(request.content)
            principal = self.principals.get(body.get("session_token") or body.get("api_key") or "")
            return (
                httpx.Response(200, json={"principal": principal})
                if principal
                else httpx.Response(401, json={"error": {"code": "AUTHENTICATION_REQUIRED"}})
            )
        if path == "/internal/v1/directory/users":
            assert request.headers["x-service-authorization"] == (
                "Bearer svc:identity-service:identity-service:directory"
            )
            if self.directory_down:
                return httpx.Response(503)
            body = json.loads(request.content)
            self.directory_calls.append(body)
            users = []
            for p in self.principals.values():
                if p["tenant_id"] != body["tenant_id"]:
                    continue
                if "user_ids" in body and p["user_id"] not in body["user_ids"]:
                    continue
                if "role" in body and body["role"] not in p["roles"]:
                    continue
                users.append(
                    {
                        "id": p["user_id"],
                        "email": f"u-{uuid.UUID(p['user_id']).hex[:8]}@example.com",
                        "display_name": None,
                        "status": "deactivated" if p["user_id"] in self.inactive else "active",
                        "roles": sorted(p["roles"]),
                    }
                )
            return httpx.Response(200, json={"users": users})
        return httpx.Response(404)


class MemoryAudit:
    def __init__(self) -> None:
        self.events: list[AuditRecord] = []

    async def record(self, event: AuditRecord) -> None:
        self.events.append(event)

    def types(self) -> list[str]:
        return [e.event_type for e in self.events]


class FailingEmail:
    async def send(self, message: object) -> None:
        raise ConnectionError("smtp down")


# --- The webhook wire ----------------------------------------------------------------------


@dataclass
class FakeDns:
    """Hostname -> addresses, changeable mid-test (DNS rebinding)."""

    records: dict[str, list[str]] = field(default_factory=lambda: {HOOK_HOST: [HOOK_ADDRESS]})

    async def resolve(self, host: str, _port: int) -> list[IPAddress]:
        try:
            return [ipaddress.ip_address(host)]
        except ValueError:
            pass
        if host not in self.records:
            raise HostResolutionError()
        return [ipaddress.ip_address(a) for a in self.records[host]]


@dataclass
class Receivers(httpx.AsyncBaseTransport):
    """Records every request that reached the network, and answers with `status`."""

    requests: list[httpx.Request] = field(default_factory=list)
    status: int = 200

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        return httpx.Response(self.status, text="receiver text is never read")


# --- The application ------------------------------------------------------------------------


@pytest.fixture(scope="session")
def issuer() -> ServiceTokenIssuer:
    return ServiceTokenIssuer(issuer="identity-service", private_key_pem=None, key_id="ntf-test")


@pytest.fixture
def tenant() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def other_tenant() -> uuid.UUID:
    return uuid.uuid4()


def app_settings(postgres: PostgresInfo, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "database_dsn": postgres.app_dsn,
        "migration_dsn": postgres.superuser_dsn,
        "log_level": "WARNING",
        "consumers_enabled": False,
        "webhook_backoff_seconds": 0.0,
    }
    values.update(overrides)
    return Settings(**values)


@dataclass
class Harness:
    app: Any
    client: httpx.AsyncClient
    identity: FakeIdentity
    audit: MemoryAudit
    email: InMemoryEmailSender
    secrets: InMemorySecretStore
    dns: FakeDns
    receivers: Receivers

    async def dispatch(self, subject: str, event: Any, key: str | None = None) -> Any:
        return await self.app.state.dispatcher.handle(
            subject, key or f"TEST:{uuid.uuid4().int % 10**9}", event.model_dump_json().encode()
        )


@pytest.fixture
async def harness(postgres: PostgresInfo, issuer: ServiceTokenIssuer) -> AsyncIterator[Harness]:
    async with running(postgres, issuer) as h:
        yield h


@asynccontextmanager
async def running(
    postgres: PostgresInfo, issuer: ServiceTokenIssuer, **overrides: Any
) -> AsyncIterator[Harness]:
    from notification_service.main import create_app

    identity = FakeIdentity()
    audit = MemoryAudit()
    email = overrides.pop("email", None) or InMemoryEmailSender()
    secrets = InMemorySecretStore()
    dns = FakeDns()
    receivers = Receivers()
    app = create_app(
        settings=app_settings(postgres, **overrides),
        http_transport=httpx.MockTransport(identity.handler),
        service_token_verifier=ServiceTokenVerifier(
            issuer="identity-service", audience="notification-service", keyset=issuer.jwks()
        ),
        secrets=secrets,
        audit=audit,
        email=email,
        webhook_transport=receivers,
        resolver=dns.resolve,
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://notification.test"
        ) as client,
    ):
        yield Harness(app, client, identity, audit, email, secrets, dns, receivers)  # type: ignore[arg-type]
