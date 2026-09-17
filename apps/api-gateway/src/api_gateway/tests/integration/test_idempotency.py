"""Section 9 Idempotency-Key at the gateway, against real Redis and fake upstreams."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from api_gateway.core.config import Settings
from api_gateway.tests.conftest import FakeUpstreams, build_client

pytestmark = [pytest.mark.integration, pytest.mark.security]

URL = "/api/v1/dashboards"
BODY = {"name": "Q2 sales"}


def _auth(user: str = "client", key: str | None = "retry-1") -> dict[str, str]:
    headers = {"Cookie": f"buvi_session={user}"}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


@pytest.fixture(autouse=True)
def _no_cookies(upstreams: FakeUpstreams) -> None:
    upstreams.set_cookies = False


def _dashboard_calls(upstreams: FakeUpstreams) -> int:
    return sum(1 for r in upstreams.proxied if r.url.path == URL)


async def test_retry_replays_the_first_response_without_calling_upstream(
    client: httpx.AsyncClient, upstreams: FakeUpstreams
) -> None:
    first = await client.post(URL, json=BODY, headers=_auth())
    second = await client.post(URL, json=BODY, headers=_auth())
    assert first.status_code == second.status_code == 201
    assert second.json() == first.json()
    assert second.headers["idempotent-replayed"] == "true"
    assert "idempotent-replayed" not in first.headers
    assert second.headers["x-request-id"] != first.headers["x-request-id"]
    assert _dashboard_calls(upstreams) == 1


async def test_without_a_key_nothing_changes(
    client: httpx.AsyncClient, upstreams: FakeUpstreams
) -> None:
    await client.post(URL, json=BODY, headers=_auth(key=None))
    await client.post(URL, json=BODY, headers=_auth(key=None))
    assert _dashboard_calls(upstreams) == 2


async def test_same_key_different_request_is_rejected_on_any_route(
    client: httpx.AsyncClient, upstreams: FakeUpstreams
) -> None:
    assert (await client.post(URL, json=BODY, headers=_auth())).status_code == 201
    other_body = await client.post(URL, json={"name": "other"}, headers=_auth())
    assert other_body.status_code == 409
    assert other_body.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    other_route = await client.post("/api/v1/conversations", json=BODY, headers=_auth())
    assert other_route.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert len(upstreams.proxied) == 1


async def test_keys_are_scoped_to_the_principal_and_tenant(
    client: httpx.AsyncClient, upstreams: FakeUpstreams
) -> None:
    for user in ("u1", "u2", "u3"):
        response = await client.post(URL, json=BODY, headers=_auth(user))
        assert response.status_code == 201 and "idempotent-replayed" not in response.headers
    assert _dashboard_calls(upstreams) == 3


async def test_authorization_runs_before_any_replay(
    client: httpx.AsyncClient, upstreams: FakeUpstreams
) -> None:
    assert (await client.post(URL, json=BODY, headers=_auth())).status_code == 201
    unauthenticated = await client.post(URL, json=BODY, headers={"Idempotency-Key": "retry-1"})
    assert unauthenticated.status_code == 401
    upstreams.principals["client"] = {**upstreams.principals["client"], "permissions": []}
    forbidden = await client.post(URL, json=BODY, headers=_auth())
    assert forbidden.status_code == 403


async def test_invalid_key_is_422(client: httpx.AsyncClient, upstreams: FakeUpstreams) -> None:
    for key in ("has space", "x" * 256):
        response = await client.post(URL, json=BODY, headers=_auth(key=key))
        assert response.status_code == 422
    assert upstreams.proxied == []


@pytest.mark.parametrize("status", [500, 502, 503, 409, 429, 403])
async def test_retryable_outcomes_are_not_recorded(
    client: httpx.AsyncClient, upstreams: FakeUpstreams, status: int
) -> None:
    upstreams.status_for[URL] = status
    assert (await client.post(URL, json=BODY, headers=_auth())).status_code == status
    del upstreams.status_for[URL]
    retried = await client.post(URL, json=BODY, headers=_auth())
    assert retried.status_code == 201 and "idempotent-replayed" not in retried.headers
    assert _dashboard_calls(upstreams) == 2


async def test_deterministic_client_errors_are_replayed(
    client: httpx.AsyncClient, upstreams: FakeUpstreams
) -> None:
    upstreams.status_for[URL] = 422
    assert (await client.post(URL, json=BODY, headers=_auth())).status_code == 422
    del upstreams.status_for[URL]
    replayed = await client.post(URL, json=BODY, headers=_auth())
    assert replayed.status_code == 422 and replayed.headers["idempotent-replayed"] == "true"


async def test_upstream_outage_releases_the_key(
    client: httpx.AsyncClient, upstreams: FakeUpstreams
) -> None:
    upstreams.down = True
    assert (await client.post(URL, json=BODY, headers=_auth())).status_code == 502
    upstreams.down = False
    assert (await client.post(URL, json=BODY, headers=_auth())).status_code == 201


async def test_concurrent_duplicate_is_409_in_progress(
    settings: Settings, upstreams: FakeUpstreams
) -> None:
    release = asyncio.Event()
    original = upstreams.handler

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == URL:
            await release.wait()
        return original(request)

    upstreams.handler = slow_handler  # type: ignore[method-assign,assignment]
    async for _, client in build_client(settings, upstreams):
        first = asyncio.create_task(client.post(URL, json=BODY, headers=_auth()))
        for _ in range(100):
            if await _pending(settings):
                break
            await asyncio.sleep(0.02)
        duplicate = await client.post(URL, json=BODY, headers=_auth())
        release.set()
        assert (await first).status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "IDEMPOTENCY_REQUEST_IN_PROGRESS"
    assert duplicate.headers["retry-after"]


async def _pending(settings: Settings) -> bool:
    import redis.asyncio as aioredis

    redis = aioredis.Redis.from_url(settings.redis_url)
    try:
        keys = [k async for k in redis.scan_iter("idem:*")]
        return bool(keys)
    finally:
        await redis.aclose()


async def test_secret_and_cookie_responses_complete_without_storing_the_body(
    client: httpx.AsyncClient, upstreams: FakeUpstreams, redis_url: str
) -> None:
    import redis as sync_redis

    api_key = await client.post("/api/v1/me/api-keys", json={"name": "ci"}, headers=_auth())
    assert api_key.status_code == 201
    stored: list[Any] = [
        sync_redis.Redis.from_url(redis_url).get(k)
        for k in sync_redis.Redis.from_url(redis_url).scan_iter("idem:*")
    ]
    assert len(stored) == 1 and b'"body": null' in stored[0]
    retry = await client.post("/api/v1/me/api-keys", json={"name": "ci"}, headers=_auth())
    assert retry.status_code == 409
    assert retry.json()["error"]["code"] == "IDEMPOTENT_REPLAY_UNAVAILABLE"
    assert sum(1 for r in upstreams.proxied if r.url.path == "/api/v1/me/api-keys") == 1

    upstreams.set_cookies = True
    cookie = await client.post(URL, json=BODY, headers=_auth(key="cookie-1"))
    assert cookie.status_code == 201
    again = await client.post(URL, json=BODY, headers=_auth(key="cookie-1"))
    assert again.json()["error"]["code"] == "IDEMPOTENT_REPLAY_UNAVAILABLE"


async def test_ignored_routes_pass_the_key_through_without_records(
    client: httpx.AsyncClient, upstreams: FakeUpstreams, redis_url: str
) -> None:
    import redis as sync_redis

    for _ in range(2):
        await client.post("/api/v1/auth/logout", headers=_auth(key="logout-1"))
    assert sum(1 for r in upstreams.proxied if r.url.path == "/api/v1/auth/logout") == 2
    assert list(sync_redis.Redis.from_url(redis_url).scan_iter("idem:*")) == []


async def test_store_outage_refuses_keyed_requests_only(upstreams: FakeUpstreams) -> None:
    settings = Settings(environment="test", redis_url="redis://127.0.0.1:1/0", log_level="WARNING")
    async for _, client in build_client(settings, upstreams):
        keyed = await client.post(URL, json=BODY, headers=_auth())
        assert keyed.status_code == 503
        assert keyed.json()["error"]["code"] == "IDEMPOTENCY_UNAVAILABLE"
        unkeyed = await client.post(URL, json=BODY, headers=_auth(key=None))
        assert unkeyed.status_code == 201
