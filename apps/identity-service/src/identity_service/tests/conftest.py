"""Integration test fixtures (Section 25).

A real Postgres runs in a container so the tests exercise the actual DDL, the
actual RLS policies and the actual constraints -- a fake or a SQLite stand-in
would silently skip the half of tenant isolation that lives in the database.

The IdP and the SMTP server are stubbed at the adapter boundary instead. Keycloak
is exercised for real by `scripts/test-login.sh`, which is the Phase A1
Definition of Done's scripted flow; forcing every integration test through a
browserless OIDC round trip would buy nothing and make the suite fragile.
"""

from __future__ import annotations

import datetime as dt
import re
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from identity_service.core.config import Settings
from identity_service.infrastructure.email.sender import InMemoryEmailSender
from identity_service.infrastructure.oidc.client import OidcIdentity, OidcTokens
from identity_service.infrastructure.secrets.store import InMemorySecretStore

SERVICE_ROOT = Path(__file__).resolve().parents[3]


class SessionHandle(str):
    """A session's cookie token (the `str` value) plus its row `id`.

    Tests pass the handle as a cookie exactly as a browser would, and use `.id`
    where an endpoint addresses the session row (e.g. `DELETE /me/sessions/{id}`).
    """

    id: uuid.UUID

    def __new__(cls, token: str, session_id: uuid.UUID) -> SessionHandle:
        handle = super().__new__(cls, token)
        handle.id = session_id
        return handle


DEMO_TENANT_SLUG = "acme"
OTHER_TENANT_SLUG = "globex"


# --- Stub adapters ------------------------------------------------------------


class StubOidcClient:
    """An IdP that returns whatever the test told it to.

    Deliberately still runs the flow's own checks -- the service verifies state
    and nonce itself, so those paths are covered even with the IdP stubbed.
    """

    def __init__(self) -> None:
        self.identity = OidcIdentity(
            subject="idp-subject-default",
            email="user@acme.example.com",
            display_name="Test User",
            tenant_slug=DEMO_TENANT_SLUG,
            roles=frozenset({"client"}),
            raw_claims={},
        )
        self.exchange_calls: list[dict[str, str]] = []
        self.ended_sessions: list[str] = []
        self.fail_exchange = False

    def authorization_url(self, *, challenge: str, state: str, nonce: str) -> str:
        return (
            "https://idp.test/realms/buvi/protocol/openid-connect/auth"
            f"?code_challenge={challenge}&code_challenge_method=S256&state={state}&nonce={nonce}"
        )

    async def exchange_code(self, *, code: str, verifier: str) -> OidcTokens:
        from identity_service.domain.errors import OidcExchangeFailedError

        if self.fail_exchange:
            raise OidcExchangeFailedError()
        self.exchange_calls.append({"code": code, "verifier": verifier})
        return OidcTokens(
            access_token="stub-access-token",
            refresh_token="stub-refresh-token",
            id_token="stub-id-token",
            expires_in=300,
        )

    async def verify_id_token(self, id_token: str, *, nonce: str | None = None) -> OidcIdentity:
        return self.identity

    async def end_session(self, refresh_token: str) -> None:
        self.ended_sessions.append(refresh_token)


# --- Postgres -------------------------------------------------------------------


APP_ROLE_PASSWORD = "devapp"


@pytest.fixture(scope="session")
def postgres_dsn() -> Iterator[str]:
    """Superuser DSN for a throwaway Postgres with the identity migration applied.

    The `buvi_app` role is created before migrating so the migration's grants
    land on it, exactly as in the compose stack (Section 19).
    """
    import asyncio

    import asyncpg
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16", driver="asyncpg") as container:
        dsn = container.get_connection_url()

        async def create_app_role() -> None:
            conn = await asyncpg.connect(dsn.replace("postgresql+asyncpg", "postgresql"))
            try:
                await conn.execute(f"CREATE ROLE buvi_app LOGIN PASSWORD '{APP_ROLE_PASSWORD}'")
            finally:
                await conn.close()

        asyncio.run(create_app_role())
        result = subprocess.run(
            ["uv", "run", "--package", "identity-service", "alembic", "upgrade", "head"],  # noqa: S607
            cwd=SERVICE_ROOT,
            check=False,
            capture_output=True,
            text=True,
            env={**_clean_env(), "IDENTITY_MIGRATION_DSN": dsn},
        )
        if result.returncode != 0:
            raise RuntimeError(f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}")
        yield dsn


def _clean_env() -> dict[str, str]:
    import os

    return {k: v for k, v in os.environ.items() if not k.startswith("IDENTITY_")}


@pytest.fixture(scope="session")
def app_dsn(postgres_dsn: str) -> str:
    """The request-path DSN: `buvi_app`, which owns nothing and cannot bypass RLS.

    Running the service as a superuser would silently skip every RLS policy,
    so a missing tenant binding would pass here and fail in production.
    """
    return re.sub(r"//[^@]+@", f"//buvi_app:{APP_ROLE_PASSWORD}@", postgres_dsn, count=1)


@pytest.fixture
def settings(postgres_dsn: str, app_dsn: str) -> Settings:
    """Test settings. `session_cookie_secure` stays on so tests see browser-real flags."""
    return Settings(
        environment="test",
        database_dsn=app_dsn,  # type: ignore[arg-type]
        migration_dsn=postgres_dsn,  # type: ignore[arg-type]
        vault_use_memory_stub=True,
        session_cookie_secure=True,
        oidc_issuer="https://idp.test/realms/buvi",
        oidc_client_id="buvi-platform",
    )


@pytest.fixture
def oidc() -> StubOidcClient:
    return StubOidcClient()


@pytest.fixture
def mailbox() -> InMemoryEmailSender:
    return InMemoryEmailSender()


@pytest.fixture
def secrets() -> InMemorySecretStore:
    return InMemorySecretStore()


@pytest.fixture
async def app(
    settings: Settings,
    oidc: StubOidcClient,
    mailbox: InMemoryEmailSender,
    secrets: InMemorySecretStore,
) -> AsyncIterator[FastAPI]:
    from identity_service.main import create_app

    application = create_app(settings=settings, secrets=secrets, oidc=oidc, email=mailbox)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client speaking to the real ASGI app.

    Everything the tests assert goes over HTTP, exactly as Phase A1 requires --
    no service object is called directly.
    """
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://identity.test"
    ) as http_client:
        yield http_client


# --- Data helpers ----------------------------------------------------------------


class Fixtures:
    """Creates tenants, users and sessions for a test.

    Writes go through the schema owner rather than the app role, because
    arranging a fixture is not a request and should not have to satisfy the
    request-path RLS policies.
    """

    def __init__(self, factory: object, secrets: InMemorySecretStore) -> None:
        self._factory = factory  # type: ignore[assignment]
        self._secrets = secrets

    async def create_tenant(self, slug: str, name: str | None = None) -> uuid.UUID:
        from sqlalchemy import text

        async with self._factory() as session:
            result = await session.execute(
                text(
                    "INSERT INTO identity.tenants (name, slug) VALUES (:name, :slug) "
                    "ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name RETURNING id"
                ),
                {"name": name or slug.title(), "slug": slug},
            )
            await session.commit()
            return uuid.UUID(str(result.scalar_one()))

    async def create_user(
        self,
        *,
        tenant_id: uuid.UUID,
        email: str,
        roles: frozenset[str],
        idp_subject: str | None = None,
        status: str = "active",
    ) -> uuid.UUID:
        from sqlalchemy import text

        subject = idp_subject or f"idp-{uuid.uuid4()}"
        async with self._factory() as session:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, false)"),
                {"tid": str(tenant_id)},
            )
            result = await session.execute(
                text(
                    "INSERT INTO identity.users "
                    "(tenant_id, idp_subject, email, display_name, status) "
                    "VALUES (:tid, :sub, :email, :name, :status) RETURNING id"
                ),
                {
                    "tid": str(tenant_id),
                    "sub": subject,
                    "email": email,
                    "name": email.split("@")[0],
                    "status": status,
                },
            )
            user_id = uuid.UUID(str(result.scalar_one()))
            for role_key in roles:
                role = await session.execute(
                    text(
                        "INSERT INTO identity.roles (tenant_id, key) VALUES (:tid, :key) "
                        "ON CONFLICT (tenant_id, key) DO UPDATE SET key = EXCLUDED.key "
                        "RETURNING id"
                    ),
                    {"tid": str(tenant_id), "key": role_key},
                )
                await session.execute(
                    text(
                        "INSERT INTO identity.user_roles (user_id, role_id) "
                        "VALUES (:uid, :rid) ON CONFLICT DO NOTHING"
                    ),
                    {"uid": str(user_id), "rid": str(role.scalar_one())},
                )
            await session.commit()
            return user_id

    async def create_session(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        mfa_verified_at: dt.datetime | None = None,
        expires_in: dt.timedelta = dt.timedelta(days=7),
    ) -> SessionHandle:
        from sqlalchemy import text

        from identity_service.domain.value_objects.tokens import generate_token, hash_token

        session_id = uuid.uuid4()
        token = generate_token()
        ref = f"tenants/{tenant_id}/sessions/{session_id}"
        await self._secrets.write(ref, {"refresh_token": "stub-refresh-token"})
        async with self._factory() as session:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, false)"),
                {"tid": str(tenant_id)},
            )
            await session.execute(
                text(
                    "INSERT INTO identity.sessions "
                    "(id, user_id, tenant_id, token_hash, idp_refresh_token_ref, expires_at, mfa_verified_at) "
                    "VALUES (:sid, :uid, :tid, :th, :ref, :exp, :mfa)"
                ),
                {
                    "sid": str(session_id),
                    "uid": str(user_id),
                    "tid": str(tenant_id),
                    "th": hash_token(token),
                    "ref": ref,
                    "exp": dt.datetime.now(dt.UTC) + expires_in,
                    "mfa": mfa_verified_at,
                },
            )
            await session.commit()
        return SessionHandle(token, session_id)

    async def session_rows(self, user_id: uuid.UUID) -> list[dict[str, object]]:
        """Every stored column of a user's sessions, read as the superuser."""
        from sqlalchemy import text

        async with self._factory() as session:
            result = await session.execute(
                text("SELECT * FROM identity.sessions WHERE user_id = :uid"),
                {"uid": str(user_id)},
            )
            return [dict(row._mapping) for row in result]

    async def audit_event_types(self, tenant_id: uuid.UUID) -> list[str]:
        from sqlalchemy import text

        async with self._factory() as session:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, false)"),
                {"tid": str(tenant_id)},
            )
            result = await session.execute(
                text(
                    "SELECT event_type FROM identity.audit_events "
                    "WHERE tenant_id = :tid ORDER BY created_at"
                ),
                {"tid": str(tenant_id)},
            )
            return [str(row[0]) for row in result.all()]


@pytest.fixture
async def fixtures(
    app: FastAPI, postgres_dsn: str, secrets: InMemorySecretStore
) -> AsyncIterator[Fixtures]:
    """Arranges data as the superuser, outside the request path's RLS."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(postgres_dsn)
    try:
        yield Fixtures(async_sessionmaker(engine, expire_on_commit=False), secrets)
    finally:
        await engine.dispose()


@pytest.fixture
async def tenant(fixtures: Fixtures) -> uuid.UUID:
    return await fixtures.create_tenant(f"{DEMO_TENANT_SLUG}-{uuid.uuid4().hex[:8]}")


@pytest.fixture
async def other_tenant(fixtures: Fixtures) -> uuid.UUID:
    return await fixtures.create_tenant(f"{OTHER_TENANT_SLUG}-{uuid.uuid4().hex[:8]}")
