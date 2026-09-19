"""Data access for the `notification` schema. Every query runs inside a tenant-bound session
(RLS); the explicit `tenant_id` filters are defense in depth."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from notification_service.infrastructure.db.models import Notification, WebhookSubscription


class NotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    # --- Deliveries ---------------------------------------------------------------------

    async def claim(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        channel: str,
        template_key: str,
        payload: dict[str, Any],
        event_key: str,
        status: str,
    ) -> Notification:
        """The row for (event, user, channel): created now, or the one an earlier delivery of
        the same event created (a redelivered event never notifies twice)."""
        await self._session.execute(
            insert(Notification)
            .values(
                tenant_id=tenant_id,
                user_id=user_id,
                channel=channel,
                template_key=template_key,
                payload=payload,
                event_key=event_key,
                status=status,
                sent_at=dt.datetime.now(dt.UTC) if status == "sent" else None,
            )
            .on_conflict_do_nothing(constraint="notifications_event_key_user_id_channel_key")
        )
        row = await self._session.scalar(
            select(Notification).where(
                Notification.event_key == event_key,
                Notification.user_id == user_id,
                Notification.channel == channel,
            )
        )
        assert row is not None  # inserted above, or already there
        return row

    async def finish(self, row: Notification, *, ok: bool, detail: str | None = None) -> None:
        values: dict[str, Any] = {"status": "sent" if ok else "failed"}
        if ok:
            values["sent_at"] = dt.datetime.now(dt.UTC)
        if detail is not None:
            values["payload"] = {**row.payload, "delivery": detail}
        await self._session.execute(
            update(Notification).where(Notification.id == row.id).values(**values)
        )

    # --- Inbox --------------------------------------------------------------------------

    async def inbox(
        self,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        unread_only: bool,
        limit: int,
        after: tuple[dt.datetime, uuid.UUID] | None,
    ) -> list[Notification]:
        statement = (
            select(Notification)
            .where(
                Notification.tenant_id == tenant_id,
                Notification.user_id == user_id,
                Notification.channel == "in_app",
            )
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(limit)
        )
        if unread_only:
            statement = statement.where(Notification.read_at.is_(None))
        if after is not None:
            created, row_id = after
            statement = statement.where(
                or_(
                    Notification.created_at < created,
                    and_(Notification.created_at == created, Notification.id < row_id),
                )
            )
        return list((await self._session.scalars(statement)).all())

    async def unread_count(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> int:
        count = await self._session.scalar(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.tenant_id == tenant_id,
                Notification.user_id == user_id,
                Notification.channel == "in_app",
                Notification.read_at.is_(None),
            )
        )
        return int(count or 0)

    async def mark_read(
        self, tenant_id: uuid.UUID, user_id: uuid.UUID, notification_id: uuid.UUID
    ) -> bool:
        """False when the id is not the caller's own in-app notification."""
        row = await self._session.scalar(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.tenant_id == tenant_id,
                Notification.user_id == user_id,
                Notification.channel == "in_app",
            )
        )
        if row is None:
            return False
        if row.read_at is None:
            await self._session.execute(
                update(Notification)
                .where(Notification.id == row.id)
                .values(status="read", read_at=dt.datetime.now(dt.UTC))
            )
        return True

    # --- Webhook subscriptions ---------------------------------------------------------

    async def add_subscription(self, subscription: WebhookSubscription) -> WebhookSubscription:
        self._session.add(subscription)
        await self._session.flush()
        await self._session.refresh(subscription)
        return subscription

    async def count_active_subscriptions(self, tenant_id: uuid.UUID) -> int:
        count = await self._session.scalar(
            select(func.count())
            .select_from(WebhookSubscription)
            .where(
                WebhookSubscription.tenant_id == tenant_id, WebhookSubscription.status == "active"
            )
        )
        return int(count or 0)

    async def list_subscriptions(self, tenant_id: uuid.UUID) -> list[WebhookSubscription]:
        return list(
            (
                await self._session.scalars(
                    select(WebhookSubscription)
                    .where(WebhookSubscription.tenant_id == tenant_id)
                    .order_by(WebhookSubscription.created_at, WebhookSubscription.id)
                )
            ).all()
        )

    async def get_subscription(
        self, tenant_id: uuid.UUID, subscription_id: uuid.UUID
    ) -> WebhookSubscription | None:
        return await self._session.scalar(
            select(WebhookSubscription).where(
                WebhookSubscription.id == subscription_id,
                WebhookSubscription.tenant_id == tenant_id,
            )
        )

    async def get_subscription_tenant_id(self, subscription_id: uuid.UUID) -> uuid.UUID | None:
        return await self._session.scalar(
            select(WebhookSubscription.tenant_id).where(WebhookSubscription.id == subscription_id)
        )

    async def disable_subscription(self, subscription: WebhookSubscription) -> None:
        await self._session.execute(
            update(WebhookSubscription)
            .where(WebhookSubscription.id == subscription.id)
            .values(status="disabled")
        )

    async def subscribers(self, tenant_id: uuid.UUID, event_type: str) -> list[WebhookSubscription]:
        return list(
            (
                await self._session.scalars(
                    select(WebhookSubscription).where(
                        WebhookSubscription.tenant_id == tenant_id,
                        WebhookSubscription.status == "active",
                        WebhookSubscription.event_types.any(event_type),
                    )
                )
            ).all()
        )
