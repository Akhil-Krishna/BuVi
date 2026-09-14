"""Client-credentials grant for workloads (Section 6.3)."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from identity_service.core.config import Settings
from identity_service.domain.errors import (
    InvalidServiceClientError,
    ServiceGrantNotAllowedError,
    UnsupportedGrantTypeError,
)
from platform_auth import ServiceTokenIssuer


@dataclass(frozen=True)
class IssuedServiceToken:
    access_token: str
    expires_in: int
    scopes: frozenset[str]


class ServiceTokenService:
    def __init__(self, *, issuer: ServiceTokenIssuer, settings: Settings) -> None:
        self._issuer = issuer
        self._settings = settings

    def exchange(
        self,
        *,
        grant_type: str,
        client_id: str,
        client_secret: str,
        audience: str,
        requested_scopes: frozenset[str],
    ) -> IssuedServiceToken:
        """Issue a token for `audience`, limited to the client's registered scopes.

        Unknown client and wrong secret fail identically and in constant time, so the
        response cannot reveal which client ids exist.
        """
        if grant_type != "client_credentials":
            raise UnsupportedGrantTypeError()
        client = self._settings.service_clients.get(client_id)
        presented = hashlib.sha256(client_secret.encode("utf-8")).hexdigest()
        expected = client.secret_sha256 if client else "0" * 64
        if not hmac.compare_digest(presented, expected) or client is None:
            raise InvalidServiceClientError()

        allowed = frozenset(client.audiences.get(audience, []))
        if not allowed:
            raise ServiceGrantNotAllowedError()
        scopes = requested_scopes or allowed
        if not scopes <= allowed:
            raise ServiceGrantNotAllowedError()

        ttl = self._settings.service_token_ttl_seconds
        token = self._issuer.issue(
            subject=client_id, audience=audience, scopes=scopes, ttl_seconds=ttl
        )
        return IssuedServiceToken(access_token=token, expires_in=ttl, scopes=scopes)
