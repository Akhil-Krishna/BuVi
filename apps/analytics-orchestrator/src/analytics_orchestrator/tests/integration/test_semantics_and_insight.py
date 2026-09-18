"""Phase A7: approved metrics ground the query; `analyze_result` sees aggregates only (ADR 0009)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from analytics_orchestrator.application.services.run_executor import ARTIFACT_NAMESPACE
from analytics_orchestrator.domain.value_objects.agent_outputs import (
    GeneratedSql,
    PlanMeasure,
    QueryPlan,
    ResultInsight,
)
from analytics_orchestrator.domain.value_objects.run_state import AnalyticsRunState
from analytics_orchestrator.tests.conftest import (
    DAILY_ROW_MARKER,
    SECTION_32,
    Harness,
    run_events,
    run_row,
)

pytestmark = pytest.mark.integration

MESSAGE = "Create a sales dashboard for Q2 with monthly revenue"


def _executed_sql(harness: Harness) -> str:
    return str(
        next(c for c in harness.services.query_calls if c["path"] == "/internal/v1/queries")[
            "body"
        ]["sql"]
    )


async def _state(platform_db: Any, run_id: str) -> AnalyticsRunState:
    return AnalyticsRunState.model_validate((await run_row(platform_db, run_id))["flow_state"])


async def test_without_a_definition_the_agent_picks_a_column(
    harness: Harness, platform_db: Any, tenant: uuid.UUID
) -> None:
    who = harness.services.add_user(tenant, {"client"})
    harness.services.add_data_source(tenant)
    run_id = await harness.start_run(who, MESSAGE)
    assert (await harness.execute(who, run_id)).json()["status"] == "completed"
    assert "sum(t.amount)" in _executed_sql(harness)
    assert "SemanticResolution" not in harness.provider.calls  # nothing approved: no model call
    assert (await _state(platform_db, run_id)).grounding.metric_ids == []


async def test_defined_revenue_metric_is_used_instead_of_guessing(
    harness: Harness, platform_db: Any, tenant: uuid.UUID
) -> None:
    """DoD: an approved "Revenue" = SUM(gross_amount) wins over the agent's own column choice."""
    who = harness.services.add_user(tenant, {"client"})
    harness.services.add_data_source(tenant)
    revenue = harness.services.add_metric(tenant, synonyms=["sales"])
    harness.services.add_metric(tenant, name="Order count", aggregation="count", column="id")
    run_id = await harness.start_run(who, MESSAGE)
    assert (await harness.execute(who, run_id)).json()["status"] == "completed"

    assert "sum(t.gross_amount) AS revenue" in _executed_sql(harness)
    assert harness.provider.calls[:2] == ["AnalyticsRequest", "SemanticResolution"]
    state = await _state(platform_db, run_id)
    assert state.grounding.metric_ids == [revenue] and state.grounding.metric_names == ["Revenue"]
    assert state.grounding.measures_from_metrics == state.grounding.measures_total == 1
    assert await run_events(platform_db, run_id) == SECTION_32
    stored = harness.services.artifacts[str(uuid.uuid5(ARTIFACT_NAMESPACE, run_id))]
    assert stored["semantic_query"]["semantic"]["metrics"] == [{"id": revenue, "name": "Revenue"}]
    assert stored["semantic_query"]["measures"][0]["column"] == "sales.orders.gross_amount"


async def test_a_plan_that_ignores_the_metric_is_repaired_then_refused(
    harness: Harness, platform_db: Any, tenant: uuid.UUID
) -> None:
    who = harness.services.add_user(tenant, {"client"})
    harness.services.add_data_source(tenant)
    harness.services.add_metric(tenant)
    guessed = QueryPlan(
        tables=["sales.orders"],
        measures=[PlanMeasure(column="sales.orders.amount", aggregation="sum", alias="revenue")],
        time_column="sales.orders.order_date",
        time_grain="month",
    )
    harness.provider.queue(QueryPlan, guessed)
    run_id = await harness.start_run(who, MESSAGE)
    assert (await harness.execute(who, run_id)).json()["status"] == "completed"
    assert harness.provider.calls.count("QueryPlan") == 2
    assert (
        "metric Revenue must be measured as sum(sales.orders.gross_amount)"
        in harness.provider.users[2 + 1]
    )
    assert "sum(t.gross_amount)" in _executed_sql(harness)

    stubborn = harness.services.add_user(tenant, {"client"})
    harness.provider.queue(QueryPlan, guessed, guessed, guessed)
    run_id = await harness.start_run(stubborn, MESSAGE)
    result = (await harness.execute(stubborn, run_id)).json()
    assert result["status"] == "failed" and result["error_code"] == "OUTPUT_INVALID"


async def test_sql_that_drifts_from_the_metric_is_regenerated(
    harness: Harness, platform_db: Any, tenant: uuid.UUID
) -> None:
    """The check runs on the SQL query-gateway validated, not on the plan."""
    who = harness.services.add_user(tenant, {"client"})
    harness.services.add_data_source(tenant)
    harness.services.add_metric(tenant)
    harness.provider.queue(
        GeneratedSql,
        GeneratedSql(
            sql="SELECT date_trunc('month', t.order_date) AS month, avg(t.gross_amount) AS revenue "
            "FROM sales.orders AS t GROUP BY 1 ORDER BY 1"
        ),
    )
    run_id = await harness.start_run(who, MESSAGE)
    assert (await harness.execute(who, run_id)).json()["status"] == "completed"
    assert harness.provider.calls.count("GeneratedSql") == 2
    assert (
        "SQL must output revenue as exactly sum(sales.orders.gross_amount)"
        in harness.provider.users[4]
    )
    assert "sum(t.gross_amount)" in _executed_sql(harness)


async def test_sql_that_keeps_modifying_the_metric_fails_closed(
    harness: Harness, platform_db: Any, tenant: uuid.UUID
) -> None:
    """A mismatch that survives the repairs fails the run: nothing is executed, nothing is shown."""
    who = harness.services.add_user(tenant, {"client"})
    harness.services.add_data_source(tenant)
    harness.services.add_metric(tenant)
    doubled = GeneratedSql(
        sql="SELECT date_trunc('month', t.order_date) AS month, sum(t.gross_amount) * 2 AS revenue "
        "FROM sales.orders AS t GROUP BY 1 ORDER BY 1"
    )
    harness.provider.queue(GeneratedSql, doubled, doubled, doubled)
    run_id = await harness.start_run(who, MESSAGE)
    result = (await harness.execute(who, run_id)).json()
    assert result["status"] == "failed" and result["error_code"] == "QUERY_REJECTED"
    assert harness.provider.calls.count("GeneratedSql") == 3  # first attempt + 2 repairs
    assert [c["path"] for c in harness.services.query_calls] == [
        "/internal/v1/queries/validate"
    ] * 3  # validated three times, executed never
    assert (await run_events(platform_db, run_id))[-2:] == ["validation.failed", "run.failed"]
    assert harness.services.artifact_posts == 0


async def test_semantic_service_outage_fails_closed(
    harness: Harness, platform_db: Any, tenant: uuid.UUID
) -> None:
    who = harness.services.add_user(tenant, {"client"})
    harness.services.add_data_source(tenant)
    harness.services.semantic_down = True
    run_id = await harness.start_run(who, MESSAGE)
    result = (await harness.execute(who, run_id)).json()
    assert result["status"] == "failed" and result["error_code"] == "UPSTREAM_UNAVAILABLE"
    assert (await run_events(platform_db, run_id))[-2:] == ["semantic.failed", "run.failed"]
    assert harness.services.query_calls == []


async def test_a_metric_outside_the_permitted_context_is_never_offered(
    harness: Harness, platform_db: Any, tenant: uuid.UUID
) -> None:
    """A definition on a table the agent may not see (hidden, other source) cannot widen access."""
    who = harness.services.add_user(tenant, {"client"})
    harness.services.add_data_source(tenant)
    harness.services.add_metric(tenant, base_table_id=str(uuid.uuid4()))
    harness.services.add_metric(tenant, name="Emails", aggregation="count", column="email")
    run_id = await harness.start_run(who, MESSAGE)
    assert (await harness.execute(who, run_id)).json()["status"] == "completed"
    assert "SemanticResolution" not in harness.provider.calls
    state = await _state(platform_db, run_id)
    assert state.semantic is not None and state.semantic.candidate_count == 0


async def test_insight_uses_aggregates_only_and_becomes_the_summary(
    harness: Harness, platform_db: Any, tenant: uuid.UUID
) -> None:
    who = harness.services.add_user(tenant, {"client"})
    harness.services.add_data_source(tenant)
    run_id = await harness.start_run(who, MESSAGE)
    assert (await harness.execute(who, run_id)).json()["status"] == "completed"
    insight_prompt = harness.provider.users[harness.provider.calls.index("ResultInsight")]
    assert "<result_stats>" in insight_prompt and DAILY_ROW_MARKER not in insight_prompt
    assert '"rows"' not in insight_prompt
    state = await _state(platform_db, run_id)
    assert state.insight is not None and state.grounding.insight_grounded is True
    stored = harness.services.artifacts[str(uuid.uuid5(ARTIFACT_NAMESPACE, run_id))]
    assert stored["summary"] == state.insight.headline
    message = await platform_db.fetchval(
        "SELECT content FROM analytics.messages WHERE run_id = $1 AND role = 'assistant'",
        uuid.UUID(run_id),
    )
    assert message == state.insight.headline


async def test_ungrounded_insight_falls_back_without_failing_the_run(
    harness: Harness, platform_db: Any, tenant: uuid.UUID
) -> None:
    """ADR 0009: a number not in the statistics is repaired, then dropped -- never shown."""
    who = harness.services.add_user(tenant, {"client"})
    harness.services.add_data_source(tenant)
    invented = ResultInsight(headline="Revenue grew 37% quarter over quarter")
    harness.provider.queue(ResultInsight, invented, invented, invented)
    run_id = await harness.start_run(who, MESSAGE)
    assert (await harness.execute(who, run_id)).json()["status"] == "completed"
    assert harness.provider.calls.count("ResultInsight") == 3
    assert "number 37 is not in the result statistics" in harness.provider.users[-2]
    state = await _state(platform_db, run_id)
    assert state.insight is None and state.grounding.insight_fallback is True
    assert state.grounding.insight_grounded is False
    stored = harness.services.artifacts[str(uuid.uuid5(ARTIFACT_NAMESPACE, run_id))]
    assert "37" not in stored["summary"] and stored["summary"].endswith("rows")
