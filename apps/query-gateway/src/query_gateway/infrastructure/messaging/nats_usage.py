"""`billing.usage.recorded` on JetStream stream `BILLING` (Sections 18.1, 23; Phase A11).

The stream config matches analytics-orchestrator's (the other producer) exactly, so whichever
starts first declares it and the other's declaration is a no-op. The event id is the JetStream
message id: a retried publish inside the dedupe window is dropped by the server.
"""

from __future__ import annotations

import logging
from typing import Any

import nats
from nats.js.api import RetentionPolicy, StreamConfig
from nats.js.errors import BadRequestError

from platform_contracts import BillingUsageRecorded
from query_gateway.core.config import BILLING_USAGE_SUBJECT

logger = logging.getLogger(__name__)


class JetStreamUsageMeter:
    def __init__(self, client: nats.NATS) -> None:
        self._client = client
        self._js = client.jetstream()

    @classmethod
    async def connect(cls, url: str, *, stream: str) -> JetStreamUsageMeter:
        client = await nats.connect(url, connect_timeout=3, max_reconnect_attempts=-1)
        js = client.jetstream()
        config = StreamConfig(
            name=stream,
            subjects=[BILLING_USAGE_SUBJECT],
            retention=RetentionPolicy.LIMITS,
            duplicate_window=120.0,
        )
        try:
            await js.add_stream(config)
        except BadRequestError:
            await js.update_stream(config)
        return cls(client)

    async def record(self, event: BillingUsageRecorded) -> None:
        if not self._client.is_connected:
            # Fail fast rather than wait out the ack timeout on every query while NATS is away.
            raise ConnectionError("usage stream disconnected")
        await self._js.publish(
            BILLING_USAGE_SUBJECT,
            event.model_dump_json().encode(),
            timeout=3.0,
            headers={"Nats-Msg-Id": str(event.event_id)},
        )

    async def close(self) -> None:
        await self._client.drain()


class UnavailableUsageMeter:
    async def record(self, event: BillingUsageRecorded) -> None:  # noqa: ARG002
        raise ConnectionError("usage stream unavailable")


class ObservedUsageMeter:
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
