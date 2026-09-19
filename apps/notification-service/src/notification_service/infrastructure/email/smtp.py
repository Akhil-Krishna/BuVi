"""SMTP delivery (Section 3: notification-service owns email; MailHog in dev).

Blocking `smtplib` runs in a worker thread (Section 20: no blocking I/O on the event loop).
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

from notification_service.application.services.ports import OutboundEmail
from notification_service.core.config import Settings


class SmtpEmailSender:
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


class InMemoryEmailSender:
    """Tests only: the messages that would have been mailed."""

    def __init__(self) -> None:
        self.sent: list[OutboundEmail] = []

    async def send(self, message: OutboundEmail) -> None:
        self.sent.append(message)
