"""A greeting, thanks, or "what can you do" gets an answer, not a failure.

Before this, the model correctly judged "hi" to be no analytics question, and the product turned
that judgement into `run.failed: This kind of request is not supported yet.` These prove a run can
now end *successfully* with just a reply -- and, as importantly, that ending that way skips
everything a data question would do.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from analytics_orchestrator.application.services.ports import ProviderResponse
from analytics_orchestrator.domain.value_objects.agent_outputs import AnalyticsRequest
from analytics_orchestrator.tests.conftest import Harness, run_events, run_row

pytestmark = pytest.mark.integration

REPLY = "Hi there! I can build charts and answer questions about your connected data."


def conversation(reply: str | None = REPLY) -> ProviderResponse[AnalyticsRequest]:
    return ProviderResponse(
        output=AnalyticsRequest.model_construct(
            intent="conversation", title="Greeting", reply=reply
        ),
        input_tokens=40,
        output_tokens=25,
        model="scripted/test",
    )


async def closing_message(platform_db: Any, run_id: str) -> str:
    return str(
        await platform_db.fetchval(
            "SELECT message FROM analytics.run_events WHERE run_id = $1 AND stage = 'run' "
            "ORDER BY seq DESC LIMIT 1",
            uuid.UUID(run_id),
        )
    )


async def test_hi_completes_with_the_reply_instead_of_failing(
    harness: Harness, platform_db: Any, tenant: uuid.UUID
) -> None:
    who = harness.services.add_user(tenant, {"developer"})
    harness.services.add_data_source(tenant)
    run_id = await harness.start_run(who, "hi")

    result = (await harness.execute(who, run_id)).json()
    assert result == {"run_id": run_id, "status": "completed", "error_code": None}
    # Only the intent step announces itself; the analytic stages never start.
    assert await run_events(platform_db, run_id) == [
        "intent.started",
        "intent.completed",
        "run.completed",
    ]
    assert "Hello" in await closing_message(platform_db, run_id)


async def test_a_conversational_run_touches_no_data_and_costs_one_model_call(
    harness: Harness, tenant: uuid.UUID
) -> None:
    who = harness.services.add_user(tenant, {"developer"})
    harness.services.add_data_source(tenant)
    run_id = await harness.start_run(who, "hello")
    await harness.execute(who, run_id)

    assert harness.provider.calls == ["AnalyticsRequest"]  # intent only: no plan, SQL or chart
    assert harness.services.query_calls == []  # never reached query-gateway
    assert harness.services.artifacts == {} and harness.services.chart_checks == []


async def test_the_reply_is_the_closing_message_and_is_kept_in_the_conversation(
    harness: Harness, platform_db: Any, tenant: uuid.UUID
) -> None:
    who = harness.services.add_user(tenant, {"developer"})
    harness.services.add_data_source(tenant)
    harness.provider.overrides.setdefault(AnalyticsRequest, []).append(conversation())
    run_id = await harness.start_run(who, "thanks a lot")
    await harness.execute(who, run_id)

    assert await closing_message(platform_db, run_id) == REPLY
    stored = await platform_db.fetchrow(
        "SELECT role, content FROM analytics.messages WHERE run_id = $1 AND role = 'assistant'",
        uuid.UUID(run_id),
    )
    assert stored is not None and stored["content"] == REPLY
    assert (await run_row(platform_db, run_id))["flow_state"]["reply"] == REPLY


async def test_a_greeting_works_before_any_data_source_exists(
    harness: Harness, tenant: uuid.UUID
) -> None:
    """The no-source check moved from the first step to schema retrieval, so a tenant that has
    connected nothing yet is still greeted -- and still told once it asks a data question."""
    who = harness.services.add_user(tenant, {"client"})
    greeted = (await harness.execute(who, await harness.start_run(who, "hi"))).json()
    assert greeted["status"] == "completed"

    asked = (await harness.execute(who, await harness.start_run(who))).json()
    assert asked["status"] == "failed" and asked["error_code"] == "NO_DATA_SOURCE"


async def test_a_real_data_question_is_unaffected(harness: Harness, tenant: uuid.UUID) -> None:
    who = harness.services.add_user(tenant, {"client"})
    harness.services.add_data_source(tenant)
    result = (await harness.execute(who, await harness.start_run(who))).json()
    assert result["status"] == "completed"
    assert harness.services.artifacts  # a chart was built


async def test_unsupported_still_fails_when_no_reply_can_be_written(
    harness: Harness, tenant: uuid.UUID
) -> None:
    who = harness.services.add_user(tenant, {"client"})
    harness.services.add_data_source(tenant)
    run_id = await harness.start_run(who, "write me a poem about cats")
    result = (await harness.execute(who, run_id)).json()
    assert result["error_code"] == "REQUEST_NOT_SUPPORTED"
