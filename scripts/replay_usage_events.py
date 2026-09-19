"""Republish usage events that a producer logged as undelivered (Section 23; ADR 0014).

query-gateway and analytics-orchestrator log `usage event undelivered` at ERROR with the whole
`billing.usage.recorded` event when NATS refused it. Feed their JSON log lines to this script
once NATS is back; it republishes each such event with its `event_id` as the JetStream message
id. worker-runtime's store is idempotent on `event_id`, so replaying a line twice (or an event
that did get through) never double-bills.

    uv run --package analytics-orchestrator python scripts/replay_usage_events.py < service.log
    ... --dry-run   # only count what would be republished
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Iterable

import nats

from platform_contracts import BillingUsageRecorded

SUBJECT = "billing.usage.recorded"


def undelivered(lines: Iterable[str]) -> list[BillingUsageRecorded]:
    events: dict[str, BillingUsageRecorded] = {}
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("message") != "usage event undelivered":
            continue
        body = (record.get("context") or {}).get("event")
        if isinstance(body, dict):
            event = BillingUsageRecorded.parse_event(body)
            events[str(event.event_id)] = event
    return list(events.values())


async def republish(events: list[BillingUsageRecorded], url: str) -> None:
    client = await nats.connect(url, connect_timeout=3)
    js = client.jetstream()
    for event in events:
        await js.publish(
            SUBJECT,
            event.model_dump_json().encode(),
            headers={"Nats-Msg-Id": str(event.event_id)},
            timeout=5.0,
        )
    await client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--nats-url", default=os.environ.get("NATS_URL", "nats://localhost:4222"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    events = undelivered(sys.stdin)
    print(f"{len(events)} undelivered usage event(s) found")
    if events and not args.dry_run:
        asyncio.run(republish(events, args.nats_url))
        print("republished")
    return 0


if __name__ == "__main__":
    sys.exit(main())
