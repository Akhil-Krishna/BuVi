"""Audit delivery to identity-service (Sections 7.3, 22, 37).

MCP registrations, approvals, grants and every invocation -- denials included -- are audited
(Section 21). `identity.audit_events` has one owner, so this service records events through
identity-service's internal API rather than writing another service's table.

Delivery retries transient failures. If it still fails, the event is logged at ERROR
(without its state) for replay and the operation stands; `mcp.invocations` keeps its own
durable row either way (the same trade-off as ADR 0004).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from mcp_gateway.core.config import SCOPE_AUDIT_WRITE
from platform_auth import IntrospectionClient, IntrospectionError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditRecord:
    tenant_id: uuid.UUID
    actor_user_id: uuid.UUID | None
    event_type: str
    resource_type: str
    resource_id: str
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None
    ip_address: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "tenant_id": str(self.tenant_id),
            "actor_user_id": str(self.actor_user_id) if self.actor_user_id else None,
            "actor_type": "user",
            "event_type": self.event_type,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "before_state": self.before_state,
            "after_state": self.after_state,
            "ip_address": self.ip_address,
        }


class AuditSink(Protocol):
    async def record(self, event: AuditRecord) -> None:
        """Deliver one event. Never raises."""
        ...


class IdentityAuditSink:
    def __init__(
        self,
        *,
        identity: IntrospectionClient,
        http: httpx.AsyncClient,
        attempts: int = 3,
        backoff_seconds: float = 0.2,
    ) -> None:
        self._identity = identity
        self._http = http
        self._attempts = attempts
        self._backoff = backoff_seconds

    async def record(self, event: AuditRecord) -> None:
        url = f"{self._identity.base_url}/internal/v1/audit-events"
        status: int | None = None
        for attempt in range(1, self._attempts + 1):
            try:
                headers = await self._identity.service_headers(SCOPE_AUDIT_WRITE)
                response = await self._http.post(url, json=event.to_payload(), headers=headers)
                status = response.status_code
                if status == httpx.codes.NO_CONTENT:
                    return
                if status < 500:
                    break  # a refusal will not succeed on retry
            except (IntrospectionError, httpx.HTTPError):
                status = None
            if attempt < self._attempts:
                await asyncio.sleep(self._backoff * attempt)
        logger.error(
            "audit event delivery failed",
            extra={
                "context": {
                    "event_type": event.event_type,
                    "resource_type": event.resource_type,
                    "resource_id": event.resource_id,
                    "tenant_id": str(event.tenant_id),
                    "status": status,
                }
            },
        )
