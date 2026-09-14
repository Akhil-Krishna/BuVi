"""api-gateway over HTTP (Phase A2 DoD): every Section 9 route routed with
authentication enforced, 501 stubs, proxy header discipline, and 429 on burst."""

from __future__ import annotations

import re

import httpx
import pytest

from api_gateway.core.config import Settings
from api_gateway.domain.catalog import CATALOG, RouteSpec
from api_gateway.tests.conftest import FakeUpstreams, build_client

pytestmark = pytest.mark.integration

COOKIE = "buvi_session"


def _url(route: RouteSpec) -> str:
    return "/api/v1" + re.sub(r"\{[^}]+\}", "abc-123", route.path)


def _id(route: RouteSpec) -> str:
    return f"{route.method} {route.path}"


async def _call(
    client: httpx.AsyncClient, route: RouteSpec, token: str | None = None
) -> httpx.Response:
    cookies = {COOKIE: token} if token else None
    body = {} if route.method in ("POST", "PATCH") else None
    return await client.request(route.method, _url(route), json=body, cookies=cookies)


# --- Authentication enforced on every protected route ------------------------------


@pytest.mark.parametrize("route", [r for r in CATALOG if not r.public], ids=_id)
async def test_every_protected_route_requires_authentication(
    client: httpx.AsyncClient, route: RouteSpec
) -> None:
    response = await _call(client, route)
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert response.json()["error"]["request_id"].startswith("req_")


@pytest.mark.parametrize("route", [r for r in CATALOG if not r.public], ids=_id)
async def test_invalid_credentials_are_rejected(
    client: httpx.AsyncClient, route: RouteSpec
) -> None:
    assert (await _call(client, route, "not-a-real-session")).status_code == 401


@pytest.mark.parametrize("route", [r for r in CATALOG if r.is_stub], ids=_id)
async def test_stubbed_routes_answer_501_after_authorization(
    client: httpx.AsyncClient, route: RouteSpec
) -> None:
    response = await _call(client, route, None if route.public else "super")
    assert response.status_code == 501, response.text
    error = response.json()["error"]
    assert error["code"] == "NOT_IMPLEMENTED"
    assert error["details"]["available_in_phase"] == route.available_in_phase


@pytest.mark.parametrize(
    "route", [r for r in CATALOG if r.permission or r.any_permission or r.role], ids=_id
)
async def test_missing_permission_or_role_is_403(
    client: httpx.AsyncClient, route: RouteSpec
) -> None:
    response = await _call(client, route, "nobody")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.parametrize("route", [r for r in CATALOG if r.step_up], ids=_id)
async def test_step_up_routes_refuse_stale_mfa(client: httpx.AsyncClient, route: RouteSpec) -> None:
    response = await _call(client, route, "stale")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "STEP_UP_REQUIRED"
    assert "max_age=300" in response.headers["www-authenticate"]


async def test_inactive_user_is_403_and_identity_failure_is_502_not_401(
    client: httpx.AsyncClient,
) -> None:
    inactive = await client.get("/api/v1/auth/session", cookies={COOKIE: "inactive"})
    assert inactive.status_code == 403 and inactive.json()["error"]["code"] == "USER_NOT_ACTIVE"
    broken = await client.get("/api/v1/auth/session", cookies={COOKIE: "boom"})
    assert broken.status_code == 502 and broken.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"


# --- Proxying to identity-service (Section 6.3) ----------------------------------------


async def test_proxy_replaces_gateway_owned_headers_and_passes_cookies_through(
    client: httpx.AsyncClient, upstreams: FakeUpstreams
) -> None:
    response = await client.post(
        "/api/v1/me/api-keys?x=1",
        json={"name": "ci"},
        cookies={COOKIE: "client"},
        headers={
            "X-Service-Authorization": "Bearer forged",
            "X-Forwarded-For": "6.6.6.6",
            "X-Request-ID": "attacker-chosen-id",
            "Idempotency-Key": "idem-1",
        },
    )
    assert response.status_code == 201, response.text
    sent = upstreams.proxied[-1]
    assert sent.url.path == "/api/v1/me/api-keys" and sent.url.query == b"x=1"
    assert (
        sent.headers["x-service-authorization"]
        == "Bearer svc:identity-service:identity-service:proxy"
    )
    assert sent.headers["x-forwarded-for"] == "127.0.0.1"
    assert sent.headers["x-request-id"].startswith("req_")
    assert sent.headers["idempotency-key"] == "idem-1"
    assert "buvi_session=client" in sent.headers["cookie"]
    assert sent.content == b'{"name":"ci"}'

    assert response.headers.get_list("set-cookie") == ["first=1; HttpOnly", "second=2; HttpOnly"]
    assert response.headers.get_list("x-request-id") == [sent.headers["x-request-id"]]
    assert "connection" not in response.headers or response.headers["connection"] != "keep-alive"

    # The introspection call itself carries the gateway's service token, not the forged one.
    assert upstreams.introspections[-1]["headers"]["x-service-authorization"].startswith(
        "Bearer svc:identity-service:"
    )


async def test_api_key_is_introspected_and_forwarded(
    client: httpx.AsyncClient, upstreams: FakeUpstreams
) -> None:
    response = await client.get(
        "/api/v1/me/api-keys", headers={"Authorization": "Bearer sk_live_key"}
    )
    assert response.status_code == 200
    assert upstreams.introspections[-1]["body"] == {"api_key": "sk_live_key"}
    assert upstreams.proxied[-1].headers["authorization"] == "Bearer sk_live_key"


async def test_public_login_redirect_and_cookie_pass_through(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/auth/login")
    assert response.status_code == 307
    assert response.headers["location"] == "https://idp.test/auth?state=s"
    assert response.headers["set-cookie"].startswith("buvi_oidc_txn=abc")


async def test_upstream_down_is_502_and_slow_is_504(
    client: httpx.AsyncClient, upstreams: FakeUpstreams
) -> None:
    upstreams.down = True
    down = await client.get("/api/v1/auth/session", cookies={COOKIE: "client"})
    assert down.status_code == 502 and down.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"
    upstreams.down, upstreams.slow = False, True
    slow = await client.get("/api/v1/auth/session", cookies={COOKIE: "client"})
    assert slow.status_code == 504 and slow.json()["error"]["code"] == "UPSTREAM_TIMEOUT"


async def test_oversized_body_is_413(settings: Settings, upstreams: FakeUpstreams) -> None:
    async for _, client in build_client(
        settings.model_copy(update={"max_request_body_bytes": 16}), upstreams
    ):
        response = await client.post(
            "/api/v1/me/api-keys", content=b"x" * 64, cookies={COOKIE: "client"}
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


async def test_edge_mints_its_own_request_id(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/api/v1/share/t", headers={"X-Request-ID": "client-supplied-12345"}
    )
    assert response.headers["x-request-id"].startswith("req_")
    assert response.headers["x-request-id"] != "client-supplied-12345"


async def test_unknown_route_uses_the_error_envelope(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist")
    assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"


# --- Rate limiting (DoD: 429 on burst) --------------------------------------------------------


async def test_burst_on_a_public_route_is_429_with_retry_after(
    settings: Settings, upstreams: FakeUpstreams
) -> None:
    tight = settings.model_copy(
        update={"rate_public_ip_capacity": 3, "rate_public_ip_refill_per_second": 0.01}
    )
    async for _, client in build_client(tight, upstreams):
        statuses = [(await client.get("/api/v1/share/t")).status_code for _ in range(5)]
        assert statuses[:3] == [501, 501, 501]
        limited = await client.get("/api/v1/share/t")
        assert limited.status_code == 429
        error = limited.json()["error"]
        assert error["code"] == "RATE_LIMITED" and error["details"]["scope"] == "ip"
        assert int(limited.headers["retry-after"]) >= 1


async def test_auth_tier_is_independent_of_the_public_tier(
    settings: Settings, upstreams: FakeUpstreams
) -> None:
    tight = settings.model_copy(
        update={"rate_auth_ip_capacity": 2, "rate_auth_ip_refill_per_second": 0.01}
    )
    async for _, client in build_client(tight, upstreams):
        assert [(await client.get("/api/v1/auth/login")).status_code for _ in range(2)] == [
            307,
            307,
        ]
        assert (await client.get("/api/v1/auth/login")).status_code == 429
        assert (await client.get("/api/v1/share/t")).status_code == 501


async def test_user_bucket_limits_one_user_not_another(
    settings: Settings, upstreams: FakeUpstreams
) -> None:
    tight = settings.model_copy(
        update={"rate_user_capacity": 2, "rate_user_refill_per_second": 0.01}
    )
    async for _, client in build_client(tight, upstreams):
        for _ in range(2):
            assert (
                await client.get("/api/v1/me/notifications", cookies={COOKIE: "u1"})
            ).status_code == 501
        limited = await client.get("/api/v1/me/notifications", cookies={COOKIE: "u1"})
        assert limited.status_code == 429 and limited.json()["error"]["details"]["scope"] == "user"
        assert (
            await client.get("/api/v1/me/notifications", cookies={COOKIE: "u2"})
        ).status_code == 501


async def test_tenant_bucket_caps_all_users_of_a_tenant(
    settings: Settings, upstreams: FakeUpstreams
) -> None:
    tight = settings.model_copy(
        update={"rate_tenant_capacity": 3, "rate_tenant_refill_per_second": 0.01}
    )
    async for _, client in build_client(tight, upstreams):
        for token in ("u1", "u2", "u1"):
            assert (
                await client.get("/api/v1/me/notifications", cookies={COOKIE: token})
            ).status_code == 501
        limited = await client.get("/api/v1/me/notifications", cookies={COOKIE: "u2"})
        assert (
            limited.status_code == 429 and limited.json()["error"]["details"]["scope"] == "tenant"
        )
        # Another tenant is unaffected.
        assert (
            await client.get("/api/v1/me/notifications", cookies={COOKIE: "u3"})
        ).status_code == 501


async def test_redis_outage_fails_open_and_reports_degraded(
    settings: Settings, upstreams: FakeUpstreams
) -> None:
    broken = settings.model_copy(update={"redis_url": "redis://127.0.0.1:1/0"})
    async for _, client in build_client(broken, upstreams):
        assert (await client.get("/api/v1/share/t")).status_code == 501
        ready = await client.get("/health/ready")
        assert ready.status_code == 200
        assert (
            ready.json()["status"] == "degraded"
            and ready.json()["checks"]["redis"] == "unavailable"
        )


async def test_health_probes(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health/live")).status_code == 200
    ready = await client.get("/health/ready")
    assert ready.status_code == 200 and ready.json()["checks"] == {
        "identity-service": "ok",
        "redis": "ok",
    }
