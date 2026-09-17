"""Gateway test fixtures: a real Redis (rate limiting is the thing under test) and
fake upstreams behind `httpx.MockTransport`, so the gateway's real HTTP clients,
service-token client and proxy code all run unmodified."""

from __future__ import annotations

import datetime as dt
import json
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from api_gateway.core.config import Settings
from platform_auth import Principal
from platform_auth.permissions import ALL_PERMISSIONS, permissions_for_roles

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"


def _principal(
    user: str,
    *,
    tenant: str = TENANT,
    roles: frozenset[str] = frozenset(),
    permissions: frozenset[str] | None = None,
    mfa_age: dt.timedelta | None = None,
    auth_method: str = "session",
) -> dict[str, Any]:
    verified_at = dt.datetime.now(dt.UTC) - mfa_age if mfa_age is not None else None
    return Principal(
        user_id=user,
        tenant_id=tenant,
        permissions=permissions if permissions is not None else permissions_for_roles(roles),
        auth_method=auth_method,  # type: ignore[arg-type]
        mfa_verified=verified_at is not None,
        session_id=f"sess-{user}",
        mfa_verified_at=verified_at,
        roles=roles,
    ).to_dict()


@dataclass
class FakeUpstreams:
    """identity-service and a generic owning service, as one mock transport."""

    principals: dict[str, dict[str, Any]] = field(default_factory=dict)
    down: bool = False
    slow: bool = False
    token_requests: list[dict[str, str]] = field(default_factory=list)
    introspections: list[dict[str, Any]] = field(default_factory=list)
    proxied: list[httpx.Request] = field(default_factory=list)
    #: Per-path status override and cookie switch for idempotency tests.
    status_for: dict[str, int] = field(default_factory=dict)
    set_cookies: bool = True

    def __post_init__(self) -> None:
        all_perms = frozenset(ALL_PERMISSIONS)
        self.principals.update(
            {
                "super": _principal(
                    "u-super",
                    roles=frozenset({"org_admin"}),
                    permissions=all_perms,
                    mfa_age=dt.timedelta(0),
                ),
                "stale": _principal(
                    "u-stale",
                    roles=frozenset({"org_admin"}),
                    permissions=all_perms,
                    mfa_age=dt.timedelta(minutes=10),
                ),
                "nobody": _principal("u-nobody", permissions=frozenset()),
                "client": _principal("u-client", roles=frozenset({"client"})),
                "u1": _principal("u-1", roles=frozenset({"client"})),
                "u2": _principal("u-2", roles=frozenset({"client"})),
                "u3": _principal("u-3", tenant=OTHER_TENANT, roles=frozenset({"client"})),
                "sk_live_key": _principal(
                    "u-key", roles=frozenset({"developer"}), auth_method="api_key"
                ),
            }
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/internal/v1/oauth/token":
            form = dict(httpx.QueryParams(request.content.decode()))
            self.token_requests.append(form)
            token = f"svc:{form['audience']}:{form.get('scope', '')}"
            return httpx.Response(
                200,
                json={
                    "access_token": token,
                    "token_type": "Bearer",
                    "expires_in": 300,
                    "scope": form.get("scope", ""),
                },
            )
        if path == "/internal/v1/introspect":
            body = json.loads(request.content)
            self.introspections.append({"body": body, "headers": dict(request.headers)})
            credential = body.get("session_token") or body.get("api_key")
            if credential == "inactive":
                return httpx.Response(
                    403, json={"error": {"code": "USER_NOT_ACTIVE", "message": "x"}}
                )
            if credential == "boom":
                return httpx.Response(
                    500, json={"error": {"code": "INTERNAL_ERROR", "message": "x"}}
                )
            if credential in self.principals:
                return httpx.Response(200, json={"principal": self.principals[credential]})
            return httpx.Response(
                401, json={"error": {"code": "AUTHENTICATION_REQUIRED", "message": "x"}}
            )
        if path == "/health/ready":
            return httpx.Response(200, json={"status": "ok"})
        if self.down:
            raise httpx.ConnectError("upstream down", request=request)
        if self.slow:
            raise httpx.ReadTimeout("upstream slow", request=request)
        self.proxied.append(request)
        if path == "/api/v1/auth/login":
            return httpx.Response(
                307,
                headers=[
                    ("location", "https://idp.test/auth?state=s"),
                    ("set-cookie", "buvi_oidc_txn=abc; HttpOnly; Secure"),
                ],
            )
        headers = [("x-request-id", "upstream-echo"), ("connection", "keep-alive")]
        if self.set_cookies:
            headers += [("set-cookie", "first=1; HttpOnly"), ("set-cookie", "second=2; HttpOnly")]
        return httpx.Response(
            self.status_for.get(path, 201 if request.method == "POST" else 200),
            headers=headers,
            json={
                "method": request.method,
                "path": path,
                "query": request.url.query.decode(),
                "call": len(self.proxied),
            },
        )


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    import redis
    from testcontainers.core.container import DockerContainer

    container = DockerContainer("redis:7").with_exposed_ports(6379)
    with container:
        url = f"redis://{container.get_container_host_ip()}:{container.get_exposed_port(6379)}/0"
        deadline = time.monotonic() + 30
        while True:
            try:
                if redis.Redis.from_url(url).ping():
                    break
            except redis.RedisError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.2)
        yield url


@pytest.fixture(autouse=True)
def _clean_redis(redis_url: str) -> None:
    import redis

    redis.Redis.from_url(redis_url).flushdb()


@pytest.fixture
def upstreams() -> FakeUpstreams:
    return FakeUpstreams()


@pytest.fixture
def settings(redis_url: str) -> Settings:
    return Settings(environment="test", redis_url=redis_url, log_level="WARNING")


async def build_client(
    settings: Settings, upstreams: FakeUpstreams
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    from api_gateway.main import create_app

    app = create_app(
        settings=settings, http_transport=httpx.MockTransport(upstreams.handler), contracts_dir=None
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://gateway.test"
        ) as client,
    ):
        yield app, client


@pytest.fixture
async def client(settings: Settings, upstreams: FakeUpstreams) -> AsyncIterator[httpx.AsyncClient]:
    async for _, http_client in build_client(settings, upstreams):
        yield http_client
