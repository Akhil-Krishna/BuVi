"""Durable JetStream pull consumers, one per consumed topic (Section 18.1; Phase A11).

* Each stream is declared with its producer's exact config (`<domain>.>`), so whichever side
  starts first creates it and the other's declaration is a no-op.
* A new durable starts at `DeliverPolicy.NEW`: notification-service does not replay the history
  it was not running for (a year-old pin is not news). An existing durable resumes where it left.
* Explicit acks after the dispatcher finished; `in_progress()` heartbeats while it works (an
  email or three webhook attempts can outlast a short ack wait).
* The event key is `<stream>:<stream sequence>`, unique per message and stable across
  redeliveries, so a redelivered event finds its notifications already recorded.
* A lost connection reconnects with back-off; readiness reports it meanwhile.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import nats
from nats.aio.client import Client
from nats.aio.msg import Msg
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy, StreamConfig
from nats.js.errors import BadRequestError

from notification_service.application.services.dispatcher import NotificationDispatcher, Outcome
from notification_service.core.config import Settings
from notification_service.domain.policies.routing import TOPICS

logger = logging.getLogger(__name__)


class TopicConsumer:
    def __init__(
        self, *, settings: Settings, dispatcher: NotificationDispatcher, subject: str
    ) -> None:
        self._settings = settings
        self._dispatcher = dispatcher
        self.subject = subject
        self.stream = TOPICS[subject][0]
        self._client: Client | None = None
        self._stopped = asyncio.Event()
        self.running = False
        self.handled = 0

    @property
    def durable(self) -> str:
        return f"{self._settings.durable_prefix}-{self.subject.replace('.', '-')}"

    async def run(self) -> None:
        delay = 1.0
        while not self._stopped.is_set():
            try:
                await self._consume()
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.running = False
                logger.error(
                    "notification consumer failed; reconnecting",
                    extra={
                        "context": {"subject": self.subject, "error_type": type(error).__name__}
                    },
                )
                with contextlib.suppress(Exception):
                    if self._client is not None:
                        await self._client.close()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stopped.wait(), timeout=delay)
                delay = min(delay * 2, 30.0)

    async def _consume(self) -> None:
        settings = self._settings
        self._client = await nats.connect(
            settings.nats_url, connect_timeout=3, max_reconnect_attempts=-1
        )
        js = self._client.jetstream()
        config = StreamConfig(
            name=self.stream,
            subjects=[f"{self.subject.split('.', 1)[0]}.>"],
            duplicate_window=120.0,
        )
        try:
            await js.add_stream(config)
        except BadRequestError:
            await js.update_stream(config)
        subscription = await js.pull_subscribe(
            self.subject,
            durable=self.durable,
            stream=self.stream,
            config=ConsumerConfig(
                ack_policy=AckPolicy.EXPLICIT,
                deliver_policy=DeliverPolicy.NEW,
                ack_wait=settings.ack_wait_seconds,
                max_deliver=settings.max_deliver,
            ),
        )
        self.running = True
        try:
            while not self._stopped.is_set():
                try:
                    messages = await subscription.fetch(
                        settings.fetch_batch, timeout=settings.fetch_timeout_seconds
                    )
                except NatsTimeoutError:
                    continue
                for message in messages:
                    await self._handle(message)
        finally:
            self.running = False

    async def _handle(self, message: Msg) -> None:
        key = f"{self.stream}:{message.metadata.sequence.stream}"
        heartbeat = asyncio.create_task(self._heartbeat(message))
        try:
            handled = await self._dispatcher.handle(self.subject, key, message.data)
        except Exception as error:
            # The database or another dependency failed mid-event; the rows already written
            # make the retry idempotent.
            logger.error(
                "notification dispatch failed; will retry",
                extra={"context": {"subject": self.subject, "error_type": type(error).__name__}},
            )
            handled = None
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
        if handled is None or handled.outcome is Outcome.RETRY:
            if message.metadata.num_delivered >= self._settings.max_deliver:
                # JetStream will not redeliver: the notification is lost -- never silently.
                logger.error(
                    "notification event abandoned: retries exhausted",
                    extra={"context": {"subject": self.subject, "event_key": key}},
                )
                await message.term()
            else:
                await message.nak(delay=self._settings.retry_seconds)
        elif handled.outcome is Outcome.DROP:
            await message.term()
        else:
            await message.ack()
        self.handled += 1

    async def _heartbeat(self, message: Msg) -> None:
        while True:
            await asyncio.sleep(max(self._settings.ack_wait_seconds / 3, 1.0))
            with contextlib.suppress(Exception):
                await message.in_progress()

    @property
    def connected(self) -> bool:
        return bool(self._client and self._client.is_connected and self.running)

    async def stop(self) -> None:
        self._stopped.set()
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.drain()
