"""identity-service's internal directory (Phase A11): recipients and their addresses."""

from __future__ import annotations

import logging
import uuid

import httpx

from notification_service.application.services.ports import (
    DirectoryUnavailableError,
    Recipient,
)
from notification_service.core.config import SCOPE_DIRECTORY
from platform_auth import IntrospectionClient, IntrospectionError

logger = logging.getLogger(__name__)


class IdentityDirectory:
    def __init__(self, *, identity: IntrospectionClient, http: httpx.AsyncClient) -> None:
        self._identity = identity
        self._http = http

    async def users(
        self,
        tenant_id: uuid.UUID,
        *,
        user_ids: tuple[uuid.UUID, ...] = (),
        role: str | None = None,
    ) -> list[Recipient]:
        body: dict[str, object] = {"tenant_id": str(tenant_id)}
        if user_ids:
            body["user_ids"] = [str(u) for u in user_ids]
        if role is not None:
            body["role"] = role
        try:
            headers = await self._identity.service_headers(SCOPE_DIRECTORY)
            response = await self._http.post(
                f"{self._identity.base_url}/internal/v1/directory/users", json=body, headers=headers
            )
        except (IntrospectionError, httpx.HTTPError):
            raise DirectoryUnavailableError() from None
        if response.status_code != 200:
            raise DirectoryUnavailableError()
        try:
            body = response.json()
            recipients = [
                Recipient(uuid.UUID(u["id"]), str(u["email"]), str(u["status"]))
                for u in body["users"]
            ]
        except (KeyError, TypeError, ValueError):
            raise DirectoryUnavailableError() from None
        if body.get("truncated"):
            # The capped list is delivered, never mistaken for complete: loud until the
            # directory pages (Phase C1).
            logger.error(
                "recipient list truncated",
                extra={
                    "context": {
                        "tenant_id": str(tenant_id),
                        "role": role,
                        "delivered_to": len(recipients),
                    }
                },
            )
        return recipients
