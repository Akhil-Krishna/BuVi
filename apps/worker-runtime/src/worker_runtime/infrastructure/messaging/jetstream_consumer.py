"""JetStream pull consumer for `analytics.run.requested` (Section 18.1).

Explicit acks. While a message is being handled, `in_progress()` heartbeats keep JetStream from
redelivering a live execution; if the process dies the heartbeats stop and the message is
redelivered after `ack_wait`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import nats
from nats.aio.client import Client
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.api import AckPolicy, ConsumerConfig, RetentionPolicy, StreamConfig
from nats.js.errors import BadRequestError

from worker_runtime.application.services.run_dispatcher import Disposition, RunDispatcher
from worker_runtime.core.config import RUN_REQUESTED_SUBJECT, Settings

logger = logging.getLogger(__name__)


class RunConsumer:
    def __init__(self, *, settings: Settings, dispatcher: RunDispatcher) -> None:
        self._settings = settings
        self._dispatcher = dispatcher
        self._client: Client | None = None
        self._stopped = asyncio.Event()
        self.running = False
        self.handled = 0

    async def run(self) -> None:
        """Consume until stopped. A lost connection or a failed setup is retried with back-off
        rather than leaving a silently dead consumer (readiness reports it meanwhile)."""
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
                    "run consumer failed; reconnecting",
                    extra={"context": {"error_type": type(error).__name__}},
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
        stream = StreamConfig(
            name=settings.run_stream,
            subjects=[RUN_REQUESTED_SUBJECT],
            retention=RetentionPolicy.LIMITS,
            duplicate_window=120.0,
        )
        try:
            await js.add_stream(stream)
        except BadRequestError:
            await js.update_stream(stream)
        subscription = await js.pull_subscribe(
            RUN_REQUESTED_SUBJECT,
            durable=settings.durable_name,
            config=ConsumerConfig(
                ack_policy=AckPolicy.EXPLICIT,
                ack_wait=settings.ack_wait_seconds,
                max_deliver=settings.max_deliver,
            ),
        )
        self.running = True
        try:
            while not self._stopped.is_set():
                try:
                    messages = await subscription.fetch(1, timeout=settings.fetch_timeout_seconds)
                except NatsTimeoutError:
                    continue
                for message in messages:
                    await self._handle(message)
        finally:
            self.running = False

    async def _handle(self, message: nats.aio.msg.Msg) -> None:
        heartbeat = asyncio.create_task(self._heartbeat(message))
        try:
            decision = await self._dispatcher.handle(
                message.data, delivery_count=message.metadata.num_delivered
            )
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
        if decision.disposition is Disposition.ACK:
            await message.ack()
        elif decision.disposition is Disposition.RETRY:
            await message.nak(delay=decision.delay_seconds)
        else:
            await message.term()
        self.handled += 1

    async def _heartbeat(self, message: nats.aio.msg.Msg) -> None:
        while True:
            await asyncio.sleep(self._settings.heartbeat_seconds)
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
