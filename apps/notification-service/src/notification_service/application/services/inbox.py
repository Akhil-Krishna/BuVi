"""The caller's in-app notifications (Section 9 `/me/notifications`; Phase A11)."""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import uuid
from dataclasses import dataclass

from notification_service.domain.errors import InvalidCursorError, NotFoundError
from notification_service.domain.policies.templates import Rendered, render
from notification_service.infrastructure.db.models import Notification
from notification_service.infrastructure.db.repositories.notification_repository import (
    NotificationRepository,
)
from platform_auth import Principal


@dataclass(frozen=True)
class InboxItem:
    notification: Notification
    text: Rendered


@dataclass(frozen=True)
class InboxPage:
    items: list[InboxItem]
    unread: int
    next_cursor: str | None


def _encode(row: Notification) -> str:
    raw = f"{row.created_at.isoformat()}|{row.id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(cursor: str) -> tuple[dt.datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        created, row_id = raw.split("|", 1)
        return dt.datetime.fromisoformat(created), uuid.UUID(row_id)
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise InvalidCursorError() from None


class InboxService:
    def __init__(self, repository: NotificationRepository) -> None:
        self._repository = repository

    async def page(
        self, principal: Principal, *, unread_only: bool, limit: int, cursor: str | None
    ) -> InboxPage:
        tenant_id, user_id = uuid.UUID(principal.tenant_id), uuid.UUID(principal.user_id)
        rows = await self._repository.inbox(
            tenant_id,
            user_id,
            unread_only=unread_only,
            limit=limit + 1,
            after=_decode(cursor) if cursor else None,
        )
        more = len(rows) > limit
        rows = rows[:limit]
        return InboxPage(
            items=[InboxItem(r, render(r.template_key, r.payload)) for r in rows],
            unread=await self._repository.unread_count(tenant_id, user_id),
            next_cursor=_encode(rows[-1]) if more and rows else None,
        )

    async def mark_read(self, principal: Principal, notification_id: uuid.UUID) -> None:
        """Someone else's notification (or an email/webhook record) is `404`."""
        found = await self._repository.mark_read(
            uuid.UUID(principal.tenant_id), uuid.UUID(principal.user_id), notification_id
        )
        if not found:
            raise NotFoundError()
