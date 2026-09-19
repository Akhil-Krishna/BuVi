"""The Section 6.7 deactivation cascade into dashboard-service.

identity-service issues service tokens, so it signs its own for the call (no network hop to
itself). Any answer but `200` is a failure: the caller then refuses to commit the deactivation.
"""

from __future__ import annotations

import uuid

import httpx

from identity_service.application.services.ports import CascadeFailedError
from identity_service.core.config import SCOPE_DASHBOARD_LIFECYCLE
from platform_auth import SERVICE_AUTH_HEADER, ServiceTokenIssuer


class DashboardLifecycleClient:
    def __init__(
        self, *, base_url: str, http: httpx.AsyncClient, issuer: ServiceTokenIssuer
    ) -> None:
        self._base = base_url.rstrip("/")
        self._http = http
        self._issuer = issuer

    async def user_deactivated(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> int:
        token = self._issuer.issue(
            subject="identity-service",
            audience="dashboard-service",
            scopes=frozenset({SCOPE_DASHBOARD_LIFECYCLE}),
        )
        try:
            response = await self._http.post(
                f"{self._base}/internal/v1/users/{user_id}/share-links/revoke",
                params={"tenant_id": str(tenant_id)},
                headers={SERVICE_AUTH_HEADER: f"Bearer {token}"},
                timeout=10.0,
            )
        except httpx.HTTPError:
            raise CascadeFailedError() from None
        if response.status_code != 200:
            raise CascadeFailedError()
        try:
            return int(response.json()["revoked"])
        except (KeyError, TypeError, ValueError):
            raise CascadeFailedError() from None
