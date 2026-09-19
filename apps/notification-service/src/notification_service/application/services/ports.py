"""Outbound ports of notification-service's application layer."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Recipient:
    user_id: uuid.UUID
    email: str
    status: str


class DirectoryUnavailableError(Exception):
    """identity-service's directory could not be read; the event is retried."""


class Directory(Protocol):
    async def users(
        self,
        tenant_id: uuid.UUID,
        *,
        user_ids: tuple[uuid.UUID, ...] = (),
        role: str | None = None,
    ) -> list[Recipient]:
        """Raises `DirectoryUnavailableError`."""
        ...


@dataclass(frozen=True)
class OutboundEmail:
    to: str
    subject: str
    body: str


class EmailSender(Protocol):
    async def send(self, message: OutboundEmail) -> None:
        """Raises on failure; the notification is then recorded `failed`."""
        ...


@dataclass(frozen=True)
class DeliveryResult:
    ok: bool
    #: `delivered`, or a fixed reason code: destination_not_allowed, unresolvable, timeout,
    #: unreachable, redirect_refused, url_invalid, status_<code>.
    detail: str
    attempts: int


class WebhookSender(Protocol):
    async def deliver(self, url: str, body: bytes, headers: dict[str, str]) -> DeliveryResult: ...
