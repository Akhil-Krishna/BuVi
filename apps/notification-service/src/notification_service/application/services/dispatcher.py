"""One consumed event -> its notifications (Section 18.1; Phase A11).

1. Parse with the topic's contract; an unknown major version or a malformed body can never
   succeed, so the message is terminated.
2. Resolve recipients through identity-service's directory (active users only).
3. For each recipient and channel, claim the (event, user, channel) row. A redelivered event finds
   its rows already there and does nothing more -- except finish an email that a crash left
   `queued`.
4. Webhooks: for an allow-listed event type, every active subscription of the tenant that asked
   for it gets one signed delivery, recorded against the subscription's creator.

Transient failures (directory, database) retry the whole event; per-delivery failures (an SMTP
error, a refused webhook) are recorded as `failed` and the event is done.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ValidationError

from notification_service.application.services.ports import (
    Directory,
    DirectoryUnavailableError,
    EmailSender,
    OutboundEmail,
    WebhookSender,
)
from notification_service.domain.policies.routing import (
    EMAIL,
    IN_APP,
    TOPICS,
    WEBHOOK,
    WEBHOOK_EVENT_TYPES,
    notification_payload,
    route_for,
)
from notification_service.domain.policies.signing import SIGNATURE_HEADER, sign
from notification_service.domain.policies.templates import render
from notification_service.infrastructure.db.models import WebhookSubscription
from notification_service.infrastructure.db.repositories.notification_repository import (
    NotificationRepository,
)
from platform_contracts import SchemaVersionError
from platform_secrets import SecretStore, SecretStoreError

logger = logging.getLogger(__name__)

RepositoryScope = Callable[[Any], AbstractAsyncContextManager[NotificationRepository]]


class Outcome(StrEnum):
    DONE = "done"
    RETRY = "retry"
    DROP = "drop"


@dataclass(frozen=True)
class Handled:
    outcome: Outcome
    notifications: int = 0


class NotificationDispatcher:
    def __init__(
        self,
        *,
        scope: RepositoryScope,
        directory: Directory,
        email: EmailSender,
        webhooks: WebhookSender,
        secrets: SecretStore,
    ) -> None:
        self._scope = scope
        self._directory = directory
        self._email = email
        self._webhooks = webhooks
        self._secrets = secrets

    async def handle(self, subject: str, event_key: str, data: bytes) -> Handled:
        topic = TOPICS.get(subject)
        if topic is None:
            return Handled(Outcome.DROP)
        contract = topic[1]
        try:
            event: BaseModel = contract.parse_event(data)  # type: ignore[attr-defined]
        except (ValidationError, SchemaVersionError, ValueError):
            logger.error(
                "dropping malformed event",
                extra={"context": {"subject": subject, "size": len(data)}},
            )
            return Handled(Outcome.DROP)
        tenant_id = event.tenant_id  # type: ignore[attr-defined]
        route = route_for(subject, event)
        payload = notification_payload(event)
        try:
            recipients = await self._directory.users(
                tenant_id, user_ids=route.user_ids, role=route.role
            )
        except DirectoryUnavailableError:
            logger.warning(
                "directory unavailable; will retry", extra={"context": {"subject": subject}}
            )
            return Handled(Outcome.RETRY)
        count = 0
        async with self._scope(tenant_id) as repository:
            for recipient in recipients:
                if recipient.status != "active":
                    continue
                for channel in route.channels:
                    row = await repository.claim(
                        tenant_id=tenant_id,
                        user_id=recipient.user_id,
                        channel=channel,
                        template_key=route.template_key,
                        payload=payload,
                        event_key=event_key,
                        status="sent" if channel == IN_APP else "queued",
                    )
                    await repository.commit()
                    count += 1
                    if channel == EMAIL and row.status == "queued":
                        await self._send_email(repository, row, recipient.email)
            if subject in WEBHOOK_EVENT_TYPES:
                for subscription in await repository.subscribers(tenant_id, subject):
                    count += await self._deliver_webhook(
                        repository, subscription, subject, event_key, payload
                    )
        return Handled(Outcome.DONE, count)

    async def _send_email(self, repository: NotificationRepository, row: Any, to: str) -> None:
        text = render(row.template_key, row.payload)
        try:
            await self._email.send(OutboundEmail(to=to, subject=text.title, body=text.body))
            await repository.finish(row, ok=True)
        except Exception as error:
            logger.warning(
                "notification email failed",
                extra={
                    "context": {"notification_id": str(row.id), "error_type": type(error).__name__}
                },
            )
            await repository.finish(row, ok=False, detail="smtp_failed")
        await repository.commit()

    async def _deliver_webhook(
        self,
        repository: NotificationRepository,
        subscription: WebhookSubscription,
        event_type: str,
        event_key: str,
        payload: dict[str, Any],
    ) -> int:
        row = await repository.claim(
            tenant_id=subscription.tenant_id,
            user_id=subscription.created_by,
            channel=WEBHOOK,
            template_key=event_type,
            payload={"subscription_id": str(subscription.id), "event": payload},
            # One delivery per (event, subscription): two subscriptions of one creator both fire.
            event_key=f"{event_key}#{subscription.id}",
            status="queued",
        )
        await repository.commit()
        if row.status != "queued":
            return 0
        try:
            stored = await self._secrets.read(subscription.signing_secret_ref)
        except SecretStoreError:
            stored = None
        secret = (stored or {}).get("signing_secret")
        if not secret:
            # Never send unsigned: a receiver could not tell it from a forgery.
            await repository.finish(row, ok=False, detail="secret_unavailable")
            await repository.commit()
            return 1
        body = json.dumps(
            {
                "id": str(row.id),
                "type": event_type,
                "created_at": dt.datetime.now(dt.UTC).isoformat(),
                "tenant_id": str(subscription.tenant_id),
                "data": payload,
            },
            separators=(",", ":"),
        ).encode()
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "BuVi-Webhooks/1",
            "X-Buvi-Event": event_type,
            "X-Buvi-Delivery": str(row.id),
            SIGNATURE_HEADER: sign(secret, int(time.time()), body),
        }
        result = await self._webhooks.deliver(subscription.url, body, headers)
        await repository.finish(row, ok=result.ok, detail=f"{result.detail}:{result.attempts}")
        await repository.commit()
        return 1
