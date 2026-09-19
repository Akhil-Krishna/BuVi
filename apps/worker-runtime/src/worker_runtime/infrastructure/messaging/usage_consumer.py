"""JetStream pull consumer for `billing.usage.recorded` (Sections 18.1, 23; Phase A11).

Fetches up to `usage_batch_size` messages, hands them to the aggregator as one batch, and acks
each only after analytics-orchestrator stored it. The stream config matches the producers'
(analytics-orchestrator, query-gateway) exactly, so declaring it here is a no-op when it exists.
"""

from __future__ import annotations

from nats.errors import TimeoutError as NatsTimeoutError
from nats.js import JetStreamContext
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy, RetentionPolicy, StreamConfig

from worker_runtime.application.services.run_dispatcher import Disposition
from worker_runtime.application.services.usage_aggregator import UsageAggregator
from worker_runtime.core.config import BILLING_USAGE_SUBJECT, Settings
from worker_runtime.infrastructure.messaging.jetstream_consumer import (
    ReconnectingConsumer,
    declare_stream,
)


class UsageConsumer(ReconnectingConsumer):
    def __init__(self, *, settings: Settings, aggregator: UsageAggregator) -> None:
        super().__init__(settings=settings)
        self._aggregator = aggregator

    async def _consume(self, js: JetStreamContext) -> None:
        settings = self._settings
        await declare_stream(
            js,
            StreamConfig(
                name=settings.billing_stream,
                subjects=[BILLING_USAGE_SUBJECT],
                retention=RetentionPolicy.LIMITS,
                duplicate_window=120.0,
            ),
        )
        subscription = await js.pull_subscribe(
            BILLING_USAGE_SUBJECT,
            durable=settings.usage_durable_name,
            config=ConsumerConfig(
                ack_policy=AckPolicy.EXPLICIT,
                ack_wait=settings.usage_ack_wait_seconds,
                # Every event since the stream began: usage is billed, never skipped.
                deliver_policy=DeliverPolicy.ALL,
                max_deliver=-1,
            ),
        )
        self.running = True
        try:
            while not self._stopped.is_set():
                try:
                    messages = await subscription.fetch(
                        settings.usage_batch_size, timeout=settings.fetch_timeout_seconds
                    )
                except NatsTimeoutError:
                    continue
                decisions = await self._aggregator.handle([m.data for m in messages])
                for message, decision in zip(messages, decisions, strict=True):
                    if decision.disposition is Disposition.ACK:
                        await message.ack()
                    elif decision.disposition is Disposition.RETRY:
                        await message.nak(delay=decision.delay_seconds)
                    else:
                        await message.term()
                    self.handled += 1
        finally:
            self.running = False
