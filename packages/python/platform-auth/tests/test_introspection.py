"""IntrospectionClient: every identity-service answer maps to one typed outcome."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import httpx
import pytest
from starlette.requests import Request

from platform_auth import (
    CredentialRejectedError,
    IdentityTimeoutError,
    IntrospectionClient,
    IntrospectionError,
    Principal,
    PrincipalNotActiveError,
    ServiceTokenClient,
    request_credentials,
)
from platform_observability import request_id_var

pytestmark = pytest.mark.unit

PRINCIPAL = Principal(
    user_id="u1",
    tenant_id="t1",
    permissions=frozenset({"data:manage"}),
    auth_method="session",
    mfa_verified=True,
    session_id="s1",
    mfa_verified_at=dt.datetime.now(dt.UTC),
    roles=frozenset({"developer"}),
)


def _client(handler: Any) -> tuple[IntrospectionClient, httpx.AsyncClient]:
    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/v1/oauth/token":
            form = dict(httpx.QueryParams(request.content.decode()))
            return httpx.Response(
                200, json={"access_token": f"svc-{form['scope']}", "expires_in": 300}
            )
        result: httpx.Response = handler(request)
        return result

    http = httpx.AsyncClient(transport=httpx.MockTransport(route))
    tokens = ServiceTokenClient(
        token_url="http://identity/internal/v1/oauth/token",
        client_id="metadata-service",
        client_secret="s",
        http=http,
    )
    return IntrospectionClient(base_url="http://identity/", http=http, tokens=tokens), http


async def test_valid_session_resolves_principal_with_service_token_and_request_id() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"principal": PRINCIPAL.to_dict()})

    client, http = _client(handler)
    token = request_id_var.set("req_0123456789abcdef")
    try:
        async with http:
            principal = await client.introspect(session_token="sess", api_key=None)
    finally:
        request_id_var.reset(token)
    assert principal == PRINCIPAL
    assert json.loads(seen[0].content) == {"session_token": "sess"}
    assert seen[0].headers["x-service-authorization"] == "Bearer svc-identity-service:introspect"
    assert seen[0].headers["x-request-id"] == "req_0123456789abcdef"


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (401, {"error": {"code": "AUTHENTICATION_REQUIRED"}}, CredentialRejectedError),
        (422, {"error": {"code": "VALIDATION_FAILED"}}, CredentialRejectedError),
        (403, {"error": {"code": "USER_NOT_ACTIVE"}}, PrincipalNotActiveError),
        (403, {"error": {"code": "FORBIDDEN"}}, IntrospectionError),
        (500, {"error": {"code": "INTERNAL_ERROR"}}, IntrospectionError),
        (200, {"principal": {"user_id": "x"}}, IntrospectionError),
    ],
)
async def test_identity_answers_map_to_typed_errors(
    status: int, body: dict[str, Any], expected: type[Exception]
) -> None:
    client, http = _client(lambda _r: httpx.Response(status, json=body))
    async with http:
        with pytest.raises(expected) as info:
            await client.introspect(session_token=None, api_key="sk_live_x")
    if expected is IntrospectionError:
        assert not isinstance(info.value, (CredentialRejectedError, PrincipalNotActiveError))


async def test_transport_failures_are_platform_faults_not_auth_failures() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    for handler, expected in ((slow, IdentityTimeoutError), (down, IntrospectionError)):
        client, http = _client(handler)
        async with http:
            with pytest.raises(expected) as info:
                await client.introspect(session_token="sess", api_key=None)
            assert not isinstance(info.value, CredentialRejectedError)
            assert not await client.ready()


async def test_missing_or_oversized_credentials_never_call_identity() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    client, http = _client(handler)
    async with http:
        for kwargs in ({"session_token": None, "api_key": None}, {"session_token": "x" * 600}):
            with pytest.raises(CredentialRejectedError):
                await client.introspect(**{"api_key": None, **kwargs})
    assert calls == []


def _request(headers: dict[str, str]) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "method": "GET", "path": "/"})


def test_request_credentials_prefers_the_session_cookie() -> None:
    both = _request({"cookie": "buvi_session=abc", "authorization": "Bearer sk_live_1"})
    assert request_credentials(both, "buvi_session") == ("abc", None)
    key = _request({"authorization": "Bearer sk_live_1"})
    assert request_credentials(key, "buvi_session") == (None, "sk_live_1")
    assert request_credentials(_request({"authorization": "Basic x"}), "buvi_session") == (
        None,
        None,
    )
