"""Phase A11: `billing.usage.recorded` events stored through the internal API (worker-runtime's
write path) and summed by `GET /billing/usage`, with seats from identity-service."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from analytics_orchestrator.core.config import SCOPE_USAGE_WRITE
from analytics_orchestrator.infrastructure.llm.scripted_provider import ScriptedProvider
from analytics_orchestrator.tests.conftest import (
    FakeServices,
    MemoryQueue,
    PostgresInfo,
    make_settings,
    running,
)
from platform_auth import ServiceTokenIssuer

pytestmark = pytest.mark.integration


def _record(tenant: uuid.UUID, metric: str, quantity: int, **extra: object) -> dict[str, object]:
    return {
        "event_id": str(uuid.uuid4()),
        "tenant_id": str(tenant),
        "metric": metric,
        "quantity": quantity,
        "occurred_at": dt.datetime.now(dt.UTC).isoformat(),
        **extra,
    }


async def test_usage_records_are_idempotent_and_summed_per_tenant(
    postgres: PostgresInfo,
    redis_url: str,
    services: FakeServices,
    provider: ScriptedProvider,
    queue: MemoryQueue,
    issuer: ServiceTokenIssuer,
    tenant: uuid.UUID,
    other_tenant: uuid.UUID,
) -> None:
    async with running(make_settings(postgres, redis_url), services, provider, queue, issuer) as h:
        worker = h.service_headers("worker-runtime", SCOPE_USAGE_WRITE)
        records = [
            _record(tenant, "llm_input_tokens", 1200, stage="intent", model="m"),
            _record(tenant, "llm_output_tokens", 300, stage="intent", model="m"),
            _record(tenant, "llm_input_tokens", 800, stage="sql", model="m"),
            _record(tenant, "query_execution_ms", 90_000),
            _record(other_tenant, "llm_input_tokens", 5000, stage="intent", model="m"),
        ]
        stored = await h.client.post(
            "/internal/v1/billing/usage-records", json={"records": records}, headers=worker
        )
        assert stored.status_code == 200, stored.text
        assert stored.json() == {"stored": 5, "duplicates": 0}
        # A redelivered batch (worker crashed before acking) is not counted twice.
        again = await h.client.post(
            "/internal/v1/billing/usage-records", json={"records": records[:2]}, headers=worker
        )
        assert again.json() == {"stored": 0, "duplicates": 2}

        wrong_scope = h.service_headers("worker-runtime", "analytics-orchestrator:execute")
        refused = await h.client.post(
            "/internal/v1/billing/usage-records", json={"records": records}, headers=wrong_scope
        )
        assert refused.status_code == 403

        billing = services.add_user(tenant, {"billing_admin"})
        services.add_user(tenant, {"client"})
        gone = services.add_user(tenant, {"developer"})
        services.inactive_users.add(str(gone.user_id))
        response = await h.client.get("/api/v1/billing/usage", headers=billing.headers)
        assert response.status_code == 200, response.text
        body = response.json()
        today = dt.datetime.now(dt.UTC).date()
        assert (body["start"], body["end"]) == (str(today.replace(day=1)), str(today))
        assert body["llm_tokens"] == {
            "input": 2000,
            "output": 300,
            "total": 2300,
            "by_stage": {"intent": 1500, "sql": 800},
        }
        assert body["query_minutes"] == 1.5
        assert body["seats"] == 2  # the deactivated user is not a seat

        # A period that ended yesterday contains nothing recorded today.
        yesterday = today - dt.timedelta(days=1)
        past = await h.client.get(
            "/api/v1/billing/usage",
            params={"start": str(yesterday), "end": str(yesterday)},
            headers=billing.headers,
        )
        assert past.json()["llm_tokens"]["total"] == 0

        for params in (
            {"start": str(today), "end": str(yesterday)},
            {"start": "2020-01-01", "end": "2021-06-01"},
        ):
            bad = await h.client.get(
                "/api/v1/billing/usage", params=params, headers=billing.headers
            )
            assert bad.status_code == 422, params

        client = services.add_user(tenant, {"client"})
        assert (
            await h.client.get("/api/v1/billing/usage", headers=client.headers)
        ).status_code == 403
