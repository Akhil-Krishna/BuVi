"""JetStream publishing (Section 18.1): `analytics.run.requested` and `billing.usage.recorded`.

Streams are declared idempotently at startup. The run message's JetStream msg-id is the run id, so
a duplicate publish within the dedupe window is dropped by the server.
"""

from __future__ import annotations

import logging
from typing import Any

import nats
from nats.aio.client import Client
from nats.js import JetStreamContext
from nats.js.api import RetentionPolicy, StreamConfig
from nats.js.errors import BadRequestError

from analytics_orchestrator.core.config import BILLING_USAGE_SUBJECT, RUN_REQUESTED_SUBJECT
from platform_contracts import BillingUsageRecorded, RunRequested

logger = logging.getLogger(__name__)


class JetStreamPublisher:
    def __init__(self, client: Client, js: JetStreamContext) -> None:
        self._client = client
        self._js = js

    @classmethod
    async def connect(cls, url: str, *, run_stream: str, billing_stream: str) -> JetStreamPublisher:
        client = await nats.connect(url, connect_timeout=3, max_reconnect_attempts=-1)
        js = client.jetstream()
        for name, subject in (
            (run_stream, RUN_REQUESTED_SUBJECT),
            (billing_stream, BILLING_USAGE_SUBJECT),
        ):
            config = StreamConfig(
                name=name,
                subjects=[subject],
                retention=RetentionPolicy.LIMITS,
                duplicate_window=120.0,
            )
            try:
                await js.add_stream(config)
            except BadRequestError:
                await js.update_stream(config)
        return cls(client, js)

    async def enqueue(self, message: RunRequested) -> None:
        await self._js.publish(
            RUN_REQUESTED_SUBJECT,
            message.model_dump_json().encode(),
            headers={"Nats-Msg-Id": str(message.run_id)},
            timeout=3.0,
        )

    async def record(self, event: BillingUsageRecorded) -> None:
        await self._js.publish(
            BILLING_USAGE_SUBJECT,
            event.model_dump_json().encode(),
            timeout=3.0,
            headers={"Nats-Msg-Id": str(event.event_id)},
        )

    @property
    def connected(self) -> bool:
        return bool(self._client.is_connected)

    async def close(self) -> None:
        await self._client.drain()


class ObservedUsageSink:
    """Usage is billed, so a lost event is never silent (Section 23; ADR 0014).

    Wraps the real publisher and never raises. An event that could not be published is logged
    at ERROR as `usage event undelivered` with the whole event -- ids and numbers only, no
    secrets -- so `scripts/replay_usage_events.py` can republish it from the logs (the
    aggregator is idempotent on `event_id`), and counted for readiness (`checks.usage`).
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.undelivered = 0

    async def record(self, event: BillingUsageRecorded) -> None:
        try:
            await self._inner.record(event)
        except Exception as error:
            self.undelivered += 1
            logger.error(
                "usage event undelivered",
                extra={
                    "context": {
                        "event": event.model_dump(mode="json"),
                        "error_type": type(error).__name__,
                    }
                },
            )
