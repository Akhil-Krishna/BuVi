"""Service-to-service authentication (Section 6.3).

Short-lived RS256 JWTs issued by identity-service's client-credentials grant, with an
explicit `aud` per callee and narrow `scope` claims. Every callee verifies signature,
issuer, audience, expiry and scope itself -- "inside the cluster" is never trusted.

Tokens travel in `X-Service-Authorization`, not `Authorization`: a proxied request
also carries the *user's* credential, and the two must never be confused.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final

import httpx
from fastapi import FastAPI, HTTPException, Request, status
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeyParameters, KeySet, RSAKey

SERVICE_AUTH_HEADER: Final = "X-Service-Authorization"
DEFAULT_TOKEN_TTL_SECONDS: Final = 300
_ALGORITHM: Final = "RS256"
_VERIFIER_ATTR = "platform_auth_service_token_verifier"


class ServiceTokenError(Exception):
    """Any reason a service token is not acceptable. Callers answer 401."""


@dataclass(frozen=True)
class ServiceIdentity:
    """A verified calling workload."""

    subject: str
    audience: str
    scopes: frozenset[str]
    token_id: str


class ServiceTokenIssuer:
    """Signs service tokens. Lives only in identity-service."""

    def __init__(self, *, issuer: str, private_key_pem: str | None, key_id: str) -> None:
        parameters: KeyParameters = {"kid": key_id, "use": "sig", "alg": _ALGORITHM}
        if private_key_pem:
            self._key = RSAKey.import_key(private_key_pem, parameters=parameters)
        else:
            # Ephemeral key: acceptable only in dev/test, where one process issues and
            # tokens live five minutes. Callers refuse this outside dev/test.
            self._key = RSAKey.generate_key(2048, parameters=parameters)
        self._issuer = issuer
        self._key_id = key_id
        self.ephemeral = not private_key_pem

    def issue(
        self,
        *,
        subject: str,
        audience: str,
        scopes: frozenset[str],
        ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
    ) -> str:
        now = int(time.time())
        claims = {
            "iss": self._issuer,
            "sub": subject,
            "aud": audience,
            "scope": " ".join(sorted(scopes)),
            "iat": now,
            "nbf": now,
            "exp": now + ttl_seconds,
            "jti": uuid.uuid4().hex,
        }
        header = {"alg": _ALGORITHM, "kid": self._key_id, "typ": "JWT"}
        return jwt.encode(header, claims, self._key)

    def jwks(self) -> dict[str, Any]:
        """Public keys only, for `/internal/v1/jwks.json`."""
        return {"keys": [self._key.as_dict(private=False)]}


class ServiceTokenVerifier:
    """Verifies service tokens addressed to one audience.

    Keys come from a static key set (tests) or the issuer's JWKS URL, cached and
    re-fetched at most every `min_refetch_seconds` when an unknown key id appears, so
    a forged `kid` cannot drive an unbounded fetch loop.
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str | None = None,
        keyset: dict[str, Any] | None = None,
        http: httpx.AsyncClient | None = None,
        cache_ttl_seconds: float = 300.0,
        min_refetch_seconds: float = 10.0,
        leeway_seconds: int = 30,
    ) -> None:
        if jwks_url is None and keyset is None:
            raise ValueError("ServiceTokenVerifier needs jwks_url or keyset")
        self._issuer = issuer
        self._audience = audience
        self._jwks_url = jwks_url
        self._http = http
        self._keyset = KeySet.import_key_set(keyset) if keyset else None  # type: ignore[arg-type]
        self._fetched_at = 0.0
        self._cache_ttl = cache_ttl_seconds
        self._min_refetch = min_refetch_seconds
        self._leeway = leeway_seconds

    async def _fetch(self) -> None:
        if self._jwks_url is None:
            return
        if self._http is None:
            raise ServiceTokenError("no HTTP client for JWKS fetch")
        try:
            response = await self._http.get(self._jwks_url, timeout=5.0)
            response.raise_for_status()
            self._keyset = KeySet.import_key_set(response.json())
            self._fetched_at = time.monotonic()
        except (httpx.HTTPError, ValueError, JoseError) as exc:
            raise ServiceTokenError("service key set unavailable") from exc

    async def _keys(self, *, force: bool = False) -> KeySet:
        age = time.monotonic() - self._fetched_at
        stale = self._jwks_url is not None and age >= self._cache_ttl
        if self._keyset is None or stale or (force and age >= self._min_refetch):
            await self._fetch()
        if self._keyset is None:
            raise ServiceTokenError("no service keys")
        return self._keyset

    async def verify(self, token: str) -> ServiceIdentity:
        try:
            decoded = jwt.decode(token, await self._keys(), algorithms=[_ALGORITHM])
        except (JoseError, ValueError):
            # Possibly a rotated key: refetch once (rate limited), then decide.
            try:
                decoded = jwt.decode(token, await self._keys(force=True), algorithms=[_ALGORITHM])
            except (JoseError, ValueError) as exc:
                raise ServiceTokenError("invalid service token") from exc

        registry = jwt.JWTClaimsRegistry(
            leeway=self._leeway,
            iss={"essential": True, "value": self._issuer},
            aud={"essential": True, "value": self._audience},
            exp={"essential": True},
            sub={"essential": True},
        )
        try:
            registry.validate(decoded.claims)
        except JoseError as exc:
            raise ServiceTokenError("service token claims rejected") from exc

        claims = decoded.claims
        return ServiceIdentity(
            subject=str(claims["sub"]),
            audience=self._audience,
            scopes=frozenset(str(claims.get("scope", "")).split()),
            token_id=str(claims.get("jti", "")),
        )


class ServiceTokenClient:
    """Obtains and caches client-credentials tokens for calling other services."""

    def __init__(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        http: httpx.AsyncClient,
        refresh_margin_seconds: int = 60,
    ) -> None:
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http
        self._margin = refresh_margin_seconds
        self._cache: dict[tuple[str, frozenset[str]], tuple[str, float]] = {}

    async def token_for(self, audience: str, scopes: frozenset[str]) -> str:
        key = (audience, scopes)
        cached = self._cache.get(key)
        if cached and cached[1] - self._margin > time.monotonic():
            return cached[0]
        response = await self._http.post(
            self._token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "audience": audience,
                "scope": " ".join(sorted(scopes)),
            },
            timeout=5.0,
        )
        if response.status_code != 200:
            raise ServiceTokenError(f"token endpoint returned {response.status_code}")
        body = response.json()
        token = str(body["access_token"])
        self._cache[key] = (token, time.monotonic() + int(body.get("expires_in", 300)))
        return token


def install_service_token_verifier(app: FastAPI, verifier: ServiceTokenVerifier) -> None:
    setattr(app.state, _VERIFIER_ATTR, verifier)


async def verify_service_request(request: Request) -> ServiceIdentity | None:
    """Verify `X-Service-Authorization` if present. Returns None when absent."""
    raw = request.headers.get(SERVICE_AUTH_HEADER)
    if raw is None:
        return None
    verifier: ServiceTokenVerifier | None = getattr(request.app.state, _VERIFIER_ATTR, None)
    if verifier is None:  # pragma: no cover - wiring error
        raise RuntimeError("install_service_token_verifier(app, verifier) was not called")
    if not raw.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Service authentication required")
    try:
        identity = await verifier.verify(raw[len("Bearer ") :].strip())
    except ServiceTokenError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="Service authentication required"
        ) from exc
    request.state.service_identity = identity
    return identity


def require_service_scope(scope: str) -> Callable[[Request], Awaitable[ServiceIdentity]]:
    """Require a verified service token carrying `scope` (Section 6.3)."""

    async def dependency(request: Request) -> ServiceIdentity:
        identity = await verify_service_request(request)
        if identity is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, detail="Service authentication required"
            )
        if scope not in identity.scopes:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Insufficient service scope")
        return identity

    return dependency
