"""Webhook subscriptions (Section 9 `/admin/webhooks`; Section 15; Phase A11).

A subscription is the tenant's approval of one destination: created by an `org_admin` with a
fresh step-up. The URL passes the registration-time Section 15 rules here and is re-checked,
resolved and pinned at every delivery. The signing secret is generated here, stored in Vault,
shown once, and never returned again.
"""

from __future__ import annotations

import contextlib
import uuid
from dataclasses import dataclass

from notification_service.domain.errors import (
    NotFoundError,
    SecretStoreUnavailableError,
    WebhookEventTypeNotAllowedError,
    WebhookLimitError,
    WebhookUrlInvalidError,
)
from notification_service.domain.policies.routing import WEBHOOK_EVENT_TYPES
from notification_service.domain.policies.signing import new_signing_secret
from notification_service.infrastructure.audit.sink import AuditRecord, AuditSink
from notification_service.infrastructure.db.models import WebhookSubscription
from notification_service.infrastructure.db.repositories.notification_repository import (
    NotificationRepository,
)
from platform_auth import Principal
from platform_egress import EgressPolicy, EndpointRejected, parse_endpoint
from platform_secrets import SecretStore, SecretStoreError


@dataclass(frozen=True)
class CreatedWebhook:
    subscription: WebhookSubscription
    signing_secret: str


def secret_ref(tenant_id: uuid.UUID, subscription_id: uuid.UUID) -> str:
    return f"tenants/{tenant_id}/notification/webhooks/{subscription_id}"


class WebhookService:
    def __init__(
        self,
        *,
        repository: NotificationRepository,
        secrets: SecretStore,
        egress: EgressPolicy,
        audit: AuditSink,
        max_per_tenant: int,
        client_ip: str | None,
    ) -> None:
        self._repository = repository
        self._secrets = secrets
        self._egress = egress
        self._audit = audit
        self._max = max_per_tenant
        self._client_ip = client_ip

    async def create(
        self, principal: Principal, *, url: str, event_types: list[str]
    ) -> CreatedWebhook:
        tenant_id, user_id = uuid.UUID(principal.tenant_id), uuid.UUID(principal.user_id)
        refused = sorted(set(event_types) - WEBHOOK_EVENT_TYPES)
        if refused:
            raise WebhookEventTypeNotAllowedError(
                refused=refused, allowed=sorted(WEBHOOK_EVENT_TYPES)
            )
        try:
            endpoint = parse_endpoint(url, self._egress)
        except EndpointRejected as rejected:
            raise WebhookUrlInvalidError(reason=rejected.reason) from None
        if await self._repository.count_active_subscriptions(tenant_id) >= self._max:
            raise WebhookLimitError(limit=self._max)
        subscription_id = uuid.uuid4()
        ref = secret_ref(tenant_id, subscription_id)
        secret = new_signing_secret()
        try:
            await self._secrets.write(ref, {"signing_secret": secret})
        except SecretStoreError:
            raise SecretStoreUnavailableError() from None
        subscription = await self._repository.add_subscription(
            WebhookSubscription(
                id=subscription_id,
                tenant_id=tenant_id,
                url=endpoint.url,
                event_types=sorted(set(event_types)),
                signing_secret_ref=ref,
                status="active",
                created_by=user_id,
            )
        )
        await self._repository.commit()
        await self._record(
            principal,
            "webhook.created",
            subscription,
            after={"url": subscription.url, "event_types": subscription.event_types},
        )
        return CreatedWebhook(subscription, secret)

    async def list(self, principal: Principal) -> list[WebhookSubscription]:
        return await self._repository.list_subscriptions(uuid.UUID(principal.tenant_id))

    async def disable(self, principal: Principal, subscription_id: uuid.UUID) -> None:
        tenant_id = uuid.UUID(principal.tenant_id)
        subscription = await self._repository.get_subscription(tenant_id, subscription_id)
        if subscription is None:
            raise NotFoundError()
        if subscription.status == "disabled":
            return
        await self._repository.disable_subscription(subscription)
        await self._repository.commit()
        # A disabled subscription is never delivered to, so an orphaned secret signs nothing.
        with contextlib.suppress(SecretStoreError):
            await self._secrets.delete(subscription.signing_secret_ref)
        await self._record(
            principal,
            "webhook.disabled",
            subscription,
            before={"status": "active"},
            after={"status": "disabled"},
        )

    async def _record(
        self,
        principal: Principal,
        event_type: str,
        subscription: WebhookSubscription,
        *,
        before: dict[str, object] | None = None,
        after: dict[str, object] | None = None,
    ) -> None:
        await self._audit.record(
            AuditRecord(
                tenant_id=subscription.tenant_id,
                actor_user_id=uuid.UUID(principal.user_id),
                event_type=event_type,
                resource_type="webhook_subscription",
                resource_id=str(subscription.id),
                before_state=before,
                after_state=after,
                ip_address=self._client_ip,
            )
        )
