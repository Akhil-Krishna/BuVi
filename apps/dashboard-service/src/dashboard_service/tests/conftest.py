"""dashboard-service test fixtures (Section 25).

Real Postgres (migration 0001, service as the RLS-bound `buvi_app`). identity-service is faked at
its HTTP boundary; visualization-service and query-gateway are injected fakes behind the same
ports the real clients implement.
"""

from __future__ import annotations

import asyncio
import datetime as dt
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
from pydantic import ValidationError

from dashboard_service.core.config import Settings
from dashboard_service.infrastructure.audit.sink import AuditRecord
from dashboard_service.infrastructure.http.clients import (
    ChartCheck,
    DependencyUnavailableError,
    ResultGoneError,
    ResultRows,
)
from platform_auth import Principal, ServiceTokenIssuer, ServiceTokenVerifier
from platform_auth.permissions import permissions_for_roles
from platform_contracts import ChartOptions, ChartSpec, DashboardTilePinned

SERVICE_ROOT = Path(__file__).resolve().parents[3]
APP_ROLE_PASSWORD = "devapp"
RESULT_SCHEMA = [
    {"field": "month", "type": "temporal"},
    {"field": "revenue", "type": "quantitative"},
]
CHART_SPEC = {
    "type": "line",
    "dataset": "artifact-result",
    "encoding": {
        "x": {"field": "month", "type": "temporal"},
        "y": {"field": "revenue", "type": "quantitative"},
    },
    "options": {"title": "Monthly Revenue", "legend": True},
}


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
        env = {k: v for k, v in os.environ.items() if not k.startswith("DASHBOARD_")}
        result = subprocess.run(
            ["uv", "run", "--package", "dashboard-service", "alembic", "upgrade", "head"],  # noqa: S607
            cwd=SERVICE_ROOT,
            check=False,
            capture_output=True,
            text=True,
            env={**env, "DASHBOARD_MIGRATION_DSN": dsn},
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

    def add_user(
        self,
        tenant_id: uuid.UUID,
        roles: set[str],
        *,
        fresh_mfa: bool = False,
        extra_permissions: frozenset[str] = frozenset(),
    ) -> Caller:
        token = f"sess-{uuid.uuid4().hex}"
        user_id = uuid.uuid4()
        self.principals[token] = Principal(
            user_id=str(user_id),
            tenant_id=str(tenant_id),
            permissions=permissions_for_roles(frozenset(roles)) | extra_permissions,
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


class FakeVisualization:
    """Behaves like visualization-service for the parts dashboard-service relies on."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.down = False

    async def check(
        self,
        chart_spec: Any,
        result_schema: list[dict[str, Any]],
        overrides: dict[str, Any] | None = None,
    ) -> ChartCheck:
        self.calls.append({"chart_spec": chart_spec, "overrides": overrides})
        if self.down:
            raise DependencyUnavailableError()
        try:
            spec = ChartSpec.model_validate(chart_spec)
            if overrides is not None:
                ChartOptions.model_validate(overrides)
                merged = {**spec.options.model_dump(by_alias=True, exclude_none=True), **overrides}
                spec = ChartSpec.model_validate({**spec.to_wire(), "options": merged})
        except ValidationError as error:
            return ChartCheck(False, None, [str(e["type"]) for e in error.errors()])
        fields = {f["field"] for f in result_schema}
        for encoding in (spec.encoding.x, spec.encoding.y, spec.encoding.color):
            if encoding is not None and encoding.field not in fields:
                return ChartCheck(False, None, ["encoding field not in result_schema"])
        return ChartCheck(True, spec.to_wire(), [])


class FakeResults:
    def __init__(self) -> None:
        self.expired: set[str] = set()
        #: Handles that report more rows than they carry (an export-sized result).
        self.row_counts: dict[str, int] = {}
        self.reads: list[tuple[uuid.UUID, str]] = []

    async def read(self, tenant_id: uuid.UUID, handle: str) -> ResultRows:
        self.reads.append((tenant_id, handle))
        if handle in self.expired:
            raise ResultGoneError()
        return ResultRows(
            columns=[
                {"name": "month", "type": "timestamp"},
                {"name": "revenue", "type": "numeric"},
            ],
            rows=[["2026-04-01T00:00:00", "1200.50"], ["2026-05-01T00:00:00", "980.00"]],
            row_count=self.row_counts.get(handle, 2),
            truncated=False,
            expires_at=(dt.datetime.now(dt.UTC) + dt.timedelta(hours=20)).isoformat(),
        )


class MemoryAudit:
    def __init__(self) -> None:
        self.events: list[AuditRecord] = []

    async def record(self, event: AuditRecord) -> None:
        self.events.append(event)


class MemoryEvents:
    def __init__(self) -> None:
        self.pinned: list[DashboardTilePinned] = []
        self.fail = False

    async def tile_pinned(self, event: DashboardTilePinned) -> None:
        if self.fail:
            raise ConnectionError("stream down")
        self.pinned.append(event)


@pytest.fixture(scope="session")
def issuer() -> ServiceTokenIssuer:
    return ServiceTokenIssuer(issuer="identity-service", private_key_pem=None, key_id="ds-test")


@pytest.fixture
def identity() -> FakeIdentity:
    return FakeIdentity()


@pytest.fixture
def visualization() -> FakeVisualization:
    return FakeVisualization()


@pytest.fixture
def results() -> FakeResults:
    return FakeResults()


@pytest.fixture
def events() -> MemoryEvents:
    return MemoryEvents()


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
    visualization: FakeVisualization
    results: FakeResults
    events: MemoryEvents
    audit: MemoryAudit

    def service_headers(
        self, subject: str = "analytics-orchestrator", scope: str = "dashboard-service:artifacts"
    ) -> dict[str, str]:
        token = self.issuer.issue(
            subject=subject, audience="dashboard-service", scopes=frozenset({scope})
        )
        return {"X-Service-Authorization": f"Bearer {token}"}

    def artifact_body(self, tenant_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
        body: dict[str, Any] = {
            "artifact_id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "conversation_id": str(uuid.uuid4()),
            "run_id": str(uuid.uuid4()),
            "title": "Monthly revenue — Q2",
            "summary": "Monthly revenue — Q2 — 3 rows",
            "semantic_query": {"tables": ["sales.orders"]},
            "source_refs": [{"data_source_id": str(uuid.uuid4()), "tables": ["sales.orders"]}],
            "validated_sql": 'SELECT 1 FROM "sales"."orders"',
            "query_result_ref": f"s3://query-results/tenants/{tenant_id}/queries/{uuid.uuid4()}.json",
            "result_schema": RESULT_SCHEMA,
            "chart_spec": CHART_SPEC,
            "created_by": str(uuid.uuid4()),
        }
        body.update(overrides)
        return body

    async def store_artifact(self, tenant_id: uuid.UUID, **overrides: Any) -> str:
        response = await self.client.post(
            "/internal/v1/artifacts",
            json=self.artifact_body(tenant_id, **overrides),
            headers=self.service_headers(),
        )
        assert response.status_code == 201, response.text
        return str(response.json()["artifact_id"])

    async def dashboard(self, who: Caller, visibility: str = "private") -> str:
        response = await self.client.post(
            "/api/v1/dashboards",
            json={"name": "Sales", "visibility": visibility},
            headers=who.headers,
        )
        assert response.status_code == 201, response.text
        return str(response.json()["id"])


@pytest.fixture
async def harness(
    postgres: PostgresInfo,
    issuer: ServiceTokenIssuer,
    identity: FakeIdentity,
    visualization: FakeVisualization,
    results: FakeResults,
    events: MemoryEvents,
) -> AsyncIterator[Harness]:
    audit = MemoryAudit()
    from dashboard_service.main import create_app

    app = create_app(
        settings=Settings(
            environment="test",
            database_dsn=postgres.app_dsn,  # type: ignore[arg-type]
            migration_dsn=postgres.superuser_dsn,  # type: ignore[arg-type]
            log_level="WARNING",
        ),
        http_transport=httpx.MockTransport(identity.handler),
        service_token_verifier=ServiceTokenVerifier(
            issuer="identity-service", audience="dashboard-service", keyset=issuer.jwks()
        ),
        visualization=visualization,
        results=results,
        events=events,
        audit=audit,
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://ds.test"
        ) as client,
    ):
        yield Harness(client, issuer, identity, visualization, results, events, audit)
