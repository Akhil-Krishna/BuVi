"""Resolve a delegated user's current principal from identity-service (Section 13, ADR 0006).

A queued analytics run has no live session. analytics-orchestrator names the user; this service
asks identity-service who that user is *now* (active? which roles?) instead of trusting anything
the caller asserts about permissions.
"""

from __future__ import annotations

import uuid

import httpx

from platform_auth import IdentityTimeoutError, IntrospectionClient, IntrospectionError, Principal
from query_gateway.core.config import SCOPE_RESOLVE_PRINCIPAL
from query_gateway.domain.errors import (
    AuthenticationRequiredError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
    UserNotActiveError,
)


class IdentityResolver:
    def __init__(self, *, identity: IntrospectionClient, http: httpx.AsyncClient) -> None:
        self._identity = identity
        self._http = http

    async def resolve(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> Principal:
        try:
            headers = await self._identity.service_headers(SCOPE_RESOLVE_PRINCIPAL)
            response = await self._http.post(
                f"{self._identity.base_url}/internal/v1/principals/resolve",
                json={"tenant_id": str(tenant_id), "user_id": str(user_id)},
                headers=headers,
            )
        except (IdentityTimeoutError, httpx.TimeoutException):
            raise UpstreamTimeoutError() from None
        except (IntrospectionError, httpx.HTTPError):
            raise UpstreamUnavailableError() from None
        if response.status_code == 200:
            try:
                principal = Principal.from_dict(response.json()["principal"])
            except (KeyError, TypeError, ValueError):
                raise UpstreamUnavailableError() from None
            if principal.tenant_id != str(tenant_id) or principal.user_id != str(user_id):
                raise UpstreamUnavailableError()
            return principal
        if response.status_code == 404:
            raise AuthenticationRequiredError()
        if response.status_code == 403:
            raise UserNotActiveError()
        raise UpstreamUnavailableError()
