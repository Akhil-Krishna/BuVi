"""Service tokens (Section 6.3) and principal wire format."""

from __future__ import annotations

import datetime as dt
import time

import httpx
import pytest
from fastapi import Depends, FastAPI
from joserfc import jwt

from platform_auth import (
    SERVICE_AUTH_HEADER,
    Principal,
    ServiceIdentity,
    ServiceTokenError,
    ServiceTokenIssuer,
    ServiceTokenVerifier,
    install_service_token_verifier,
    require_service_scope,
)

pytestmark = pytest.mark.unit

ISSUER = "identity-service"


def _issuer(key_id: str = "k1") -> ServiceTokenIssuer:
    return ServiceTokenIssuer(issuer=ISSUER, private_key_pem=None, key_id=key_id)


def _verifier(
    issuer: ServiceTokenIssuer, audience: str = "identity-service"
) -> ServiceTokenVerifier:
    return ServiceTokenVerifier(issuer=ISSUER, audience=audience, keyset=issuer.jwks())


async def test_valid_token_verifies_with_scopes() -> None:
    issuer = _issuer()
    token = issuer.issue(
        subject="api-gateway", audience="identity-service", scopes=frozenset({"a", "b"})
    )
    identity = await _verifier(issuer).verify(token)
    assert identity.subject == "api-gateway"
    assert identity.scopes == frozenset({"a", "b"})


async def test_token_for_another_audience_is_rejected() -> None:
    issuer = _issuer()
    token = issuer.issue(subject="api-gateway", audience="query-gateway", scopes=frozenset({"x"}))
    with pytest.raises(ServiceTokenError):
        await _verifier(issuer, audience="identity-service").verify(token)


async def test_token_signed_by_another_key_is_rejected() -> None:
    token = _issuer("k1").issue(subject="s", audience="identity-service", scopes=frozenset())
    with pytest.raises(ServiceTokenError):
        await _verifier(_issuer("k1")).verify(token)


async def test_expired_token_is_rejected() -> None:
    issuer = _issuer()
    token = issuer.issue(
        subject="s", audience="identity-service", scopes=frozenset(), ttl_seconds=-120
    )
    with pytest.raises(ServiceTokenError):
        await _verifier(issuer).verify(token)


async def test_unsigned_alg_none_token_is_rejected() -> None:
    now = int(time.time())
    forged = jwt.encode(
        {"alg": "none"},
        {"iss": ISSUER, "sub": "s", "aud": "identity-service", "exp": now + 60},
        None,  # type: ignore[arg-type]
        algorithms=["none"],
    )
    with pytest.raises(ServiceTokenError):
        await _verifier(_issuer()).verify(forged)


async def test_scope_dependency_401_without_token_403_without_scope() -> None:
    issuer = _issuer()
    app = FastAPI()
    install_service_token_verifier(app, _verifier(issuer))

    @app.get("/internal")
    async def internal(
        identity: ServiceIdentity = Depends(require_service_scope("need")),
    ) -> dict[str, str]:
        return {"sub": identity.subject}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/internal")).status_code == 401
        wrong = issuer.issue(subject="s", audience="identity-service", scopes=frozenset({"other"}))
        assert (
            await c.get("/internal", headers={SERVICE_AUTH_HEADER: f"Bearer {wrong}"})
        ).status_code == 403
        right = issuer.issue(subject="s", audience="identity-service", scopes=frozenset({"need"}))
        ok = await c.get("/internal", headers={SERVICE_AUTH_HEADER: f"Bearer {right}"})
        assert ok.status_code == 200 and ok.json() == {"sub": "s"}


def test_principal_round_trips_through_its_wire_form() -> None:
    original = Principal(
        user_id="u",
        tenant_id="t",
        permissions=frozenset({"chat:use"}),
        auth_method="session",
        mfa_verified=True,
        session_id="s",
        mfa_verified_at=dt.datetime(2026, 9, 14, tzinfo=dt.UTC),
        roles=frozenset({"client"}),
    )
    assert Principal.from_dict(original.to_dict()) == original


def test_principal_rejects_unknown_auth_method() -> None:
    with pytest.raises(ValueError):
        Principal.from_dict({"user_id": "u", "tenant_id": "t", "auth_method": "root"})
