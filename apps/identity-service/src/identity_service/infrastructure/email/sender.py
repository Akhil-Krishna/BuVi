"""Transactional email for the invitation flow (Sections 6.5, 6.7).

Section 3 gives email to notification-service, which arrives in Phase A11. Phase
A1's Definition of Done requires an invitation to reach a MailHog inbox before
that service exists, so identity-service owns a deliberately minimal SMTP
adapter behind the `EmailSender` protocol. Phase A11 replaces the
implementation, not the call sites. See ADR 0002.
"""

from __future__ import annotations

import asyncio
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol

from identity_service.core.config import Settings


@dataclass(frozen=True)
class OutboundEmail:
    to: str
    subject: str
    body: str


class EmailSender(Protocol):
    async def send(self, message: OutboundEmail) -> None: ...


class SmtpEmailSender:
    """Blocking `smtplib` call moved to a worker thread.

    Section 20 forbids blocking I/O inside an async handler; `asyncio.to_thread`
    keeps the event loop free without introducing another dependency.
    """

    def __init__(self, settings: Settings) -> None:
        self._host = settings.smtp_host
        self._port = settings.smtp_port
        self._use_tls = settings.smtp_use_tls
        self._username = settings.smtp_username
        self._password = (
            settings.smtp_password.get_secret_value() if settings.smtp_password else None
        )
        self._from = settings.email_from

    def _send_sync(self, message: OutboundEmail) -> None:
        email = EmailMessage()
        email["From"] = self._from
        email["To"] = message.to
        email["Subject"] = message.subject
        email.set_content(message.body)
        with smtplib.SMTP(self._host, self._port, timeout=10) as smtp:
            if self._use_tls:
                smtp.starttls()
            if self._username and self._password:
                smtp.login(self._username, self._password)
            smtp.send_message(email)

    async def send(self, message: OutboundEmail) -> None:
        await asyncio.to_thread(self._send_sync, message)


@dataclass
class InMemoryEmailSender:
    """Captures messages so tests can assert on them without an SMTP server."""

    sent: list[OutboundEmail] = field(default_factory=list)

    async def send(self, message: OutboundEmail) -> None:
        self.sent.append(message)


def build_invitation_email(
    *,
    to: str,
    tenant_name: str,
    role_key: str,
    accept_url: str,
    token: str,
    expires_in_days: int,
) -> OutboundEmail:
    """Render the invitation email.

    The token is the credential, so it appears only here and in the recipient's
    inbox -- never in a log line or an audit record.
    """
    link = f"{accept_url}?token={token}"
    body = (
        f"You have been invited to join {tenant_name} on BuVi as a {role_key}.\n\n"
        f"Accept the invitation:\n{link}\n\n"
        f"This link can be used once and expires in {expires_in_days} days.\n"
        f"If you were not expecting this invitation, ignore this message.\n"
    )
    return OutboundEmail(
        to=to,
        subject=f"You have been invited to {tenant_name} on BuVi",
        body=body,
    )
