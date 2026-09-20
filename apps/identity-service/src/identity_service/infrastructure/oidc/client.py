"""OIDC Authorization Code + PKCE client (Section 6.1).

Everything token-shaped stays server-side. The browser sees an authorization
redirect and, later, a session cookie -- never an access token, refresh token or
ID token (Section 6.2).

Discovery, JWKS fetching and signature verification are delegated to
`authlib`/`joserfc` rather than hand-rolled, per Section 6's rule that the
platform speaks standard OIDC and never a bespoke token protocol.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from joserfc import jwt
from joserfc.jwk import KeySet

from identity_service.core.config import Settings
from identity_service.domain.errors import OidcExchangeFailedError


@dataclass(frozen=True)
class OidcTokens:
    """The token set returned by the IdP's token endpoint."""

    access_token: str
    refresh_token: str | None
    id_token: str
    expires_in: int


@dataclass(frozen=True)
class OidcIdentity:
    """The claims this service trusts after verifying an ID token.

    `tenant_id` and `roles` are the claims Section 6.1 step 8 requires the
    gateway to validate on every request. Keycloak emits them from the mappers
    provisioned by `scripts/keycloak-bootstrap.sh`.
    """

    subject: str
    email: str
    display_name: str
    tenant_slug: str | None
    roles: frozenset[str]
    raw_claims: dict[str, Any]


class OidcClient(Protocol):
    def authorization_url(
        self, *, challenge: str, state: str, nonce: str, redirect_uri: str
    ) -> str: ...

    async def exchange_code(self, *, code: str, verifier: str, redirect_uri: str) -> OidcTokens: ...

    async def verify_id_token(self, id_token: str, *, nonce: str | None = None) -> OidcIdentity: ...

    async def end_session(self, refresh_token: str) -> None: ...


class KeycloakOidcClient:
    """Talks to a Keycloak realm over standard OIDC endpoints."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._client = client
        self._issuer = settings.oidc_issuer.rstrip("/")
        self._client_id = settings.oidc_client_id
        self._client_secret = settings.oidc_client_secret.get_secret_value()
        self._scopes = settings.oidc_scopes
        self._metadata: dict[str, Any] | None = None
        self._jwks: KeySet | None = None
        self._jwks_fetched_at: float = 0.0
        self._jwks_ttl_seconds = 300.0

    # --- discovery ------------------------------------------------------

    async def _discover(self) -> dict[str, Any]:
        if self._metadata is None:
            response = await self._client.get(f"{self._issuer}/.well-known/openid-configuration")
            response.raise_for_status()
            self._metadata = dict(response.json())
        return self._metadata

    async def _keyset(self) -> KeySet:
        """Fetch and cache the realm's JWKS.

        Re-fetched on a TTL so a key rotation at the IdP does not require a
        restart, and so a forged `kid` cannot force an unbounded fetch loop.
        """
        now = time.monotonic()
        if self._jwks is not None and (now - self._jwks_fetched_at) < self._jwks_ttl_seconds:
            return self._jwks
        metadata = await self._discover()
        response = await self._client.get(str(metadata["jwks_uri"]))
        response.raise_for_status()
        self._jwks = KeySet.import_key_set(response.json())
        self._jwks_fetched_at = now
        return self._jwks

    # --- flow -----------------------------------------------------------

    def authorization_url(
        self, *, challenge: str, state: str, nonce: str, redirect_uri: str
    ) -> str:
        """Build the authorization request (step 2 of Section 6.1).

        Uses the issuer's conventional endpoint rather than awaiting discovery,
        so building a redirect never blocks on an IdP round trip. `redirect_uri` is
        the caller's choice (validated by `AuthService` against its two-value
        allow-list, ADR 0018) -- this client trusts its caller and does not
        re-validate, the same division of responsibility as every other domain check.
        """
        params = httpx.QueryParams(
            {
                "client_id": self._client_id,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "scope": self._scopes,
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{self._issuer}/protocol/openid-connect/auth?{params}"

    async def exchange_code(self, *, code: str, verifier: str, redirect_uri: str) -> OidcTokens:
        """Redeem the authorization code (step 5 of Section 6.1).

        Server-to-server only. A failure never surfaces the IdP's response body:
        it can name the realm, client and grant configuration (Section 21).
        `redirect_uri` MUST be byte-identical to the one used in `authorization_url`
        for this same transaction -- OAuth requires it, and Keycloak enforces it.
        """
        metadata = await self._discover()
        response = await self._client.post(
            str(metadata["token_endpoint"]),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code_verifier": verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code >= 400:
            raise OidcExchangeFailedError()
        payload = response.json()
        if "id_token" not in payload or "access_token" not in payload:
            raise OidcExchangeFailedError()
        return OidcTokens(
            access_token=str(payload["access_token"]),
            refresh_token=payload.get("refresh_token"),
            id_token=str(payload["id_token"]),
            expires_in=int(payload.get("expires_in", 300)),
        )

    async def verify_id_token(self, id_token: str, *, nonce: str | None = None) -> OidcIdentity:
        """Verify signature, issuer, audience, expiry and nonce.

        Every one of these is required: skipping audience lets a token minted
        for another client in the same realm authenticate here, and skipping
        nonce reopens the replay window PKCE does not cover.
        """
        keyset = await self._keyset()
        try:
            decoded = jwt.decode(id_token, keyset)
        except Exception as exc:
            raise OidcExchangeFailedError() from exc

        claims = dict(decoded.claims)
        registry = jwt.JWTClaimsRegistry(
            iss={"essential": True, "value": self._issuer},
            aud={"essential": True, "value": self._client_id},
            exp={"essential": True},
            sub={"essential": True},
        )
        try:
            registry.validate(claims)
        except Exception as exc:
            raise OidcExchangeFailedError() from exc

        if nonce is not None and claims.get("nonce") != nonce:
            raise OidcExchangeFailedError()

        return _identity_from_claims(claims)

    async def end_session(self, refresh_token: str) -> None:
        """Revoke the session at the IdP (Section 6.1 step 9, Section 9 logout).

        Best-effort by design: the local session is already gone by the time
        this runs, so an IdP outage must not turn logout into an error.
        """
        metadata = await self._discover()
        endpoint = metadata.get("end_session_endpoint")
        if not endpoint:
            return
        try:
            await self._client.post(
                str(endpoint),
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": refresh_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.HTTPError:
            return


def _identity_from_claims(claims: dict[str, Any]) -> OidcIdentity:
    """Project verified claims onto the identity this service consumes."""
    realm_access = claims.get("realm_access") or {}
    roles = realm_access.get("roles") if isinstance(realm_access, dict) else None
    return OidcIdentity(
        subject=str(claims["sub"]),
        email=str(claims.get("email", "")),
        display_name=str(claims.get("name") or claims.get("preferred_username") or ""),
        tenant_slug=(str(claims["tenant_slug"]) if claims.get("tenant_slug") else None),
        roles=frozenset(str(role) for role in roles) if isinstance(roles, list) else frozenset(),
        raw_claims=claims,
    )
