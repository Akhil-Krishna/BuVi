"""Semantic grounding (Phase A7) and insight grounding (ADR 0009) rules."""

from __future__ import annotations

import pytest

from analytics_orchestrator.domain.policies.result_policy import insight_problems, result_stats
from analytics_orchestrator.domain.policies.semantic_policy import (
    metric_measure,
    resolution_problems,
    semantic_candidates,
    semantic_plan_problems,
    sql_metric_problems,
)
from analytics_orchestrator.domain.value_objects.agent_outputs import (
    PlanMeasure,
    QueryPlan,
    ResultInsight,
    SemanticResolution,
)
from analytics_orchestrator.domain.value_objects.run_state import ContextColumn, ContextTable
from platform_contracts import ResultField

pytestmark = pytest.mark.unit

ORDERS = ContextTable(
    id="t-orders",
    schema_name="sales",
    table_name="orders",
    columns=[
        ContextColumn(id="c-amount", name="amount", data_type="numeric"),
        ContextColumn(id="c-status", name="status", data_type="text"),
        ContextColumn(id="c-date", name="order_date", data_type="date"),
    ],
)
M_REVENUE = "11111111-1111-1111-1111-111111111111"
M_HIDDEN = "22222222-2222-2222-2222-222222222222"
D_STATUS = "33333333-3333-3333-3333-333333333333"


def _candidates():  # type: ignore[no-untyped-def]
    return semantic_candidates(
        [
            {
                "id": M_REVENUE,
                "name": "Net Revenue (USD)",
                "aggregation": "sum",
                "column": "amount",
                "base_table_id": "t-orders",
            },
            {
                "id": M_HIDDEN,
                "name": "Hidden",
                "aggregation": "sum",
                "column": "amount",
                "base_table_id": "t-other",
            },
            {
                "id": "x",
                "name": "PII",
                "aggregation": "count",
                "column": "email",
                "base_table_id": "t-orders",
            },
            {
                "id": "y",
                "name": "Weird",
                "aggregation": "median",
                "column": "amount",
                "base_table_id": "t-orders",
            },
        ],
        [
            {"id": D_STATUS, "name": "Status", "column_id": "c-status"},
            {"id": "z", "name": "Gone", "column_id": "c-unknown"},
        ],
        [ORDERS],
    )


def test_candidates_stay_inside_the_permitted_context() -> None:
    candidates = _candidates()
    assert [m.id for m in candidates.metrics] == [M_REVENUE]
    assert candidates.metrics[0].column == "sales.orders.amount"
    assert candidates.metrics[0].alias == "net_revenue_usd"
    assert [d.column for d in candidates.dimensions] == ["sales.orders.status"]
    assert set(candidates.tables) == {"sales.orders"}


def test_resolution_ids_must_be_candidates() -> None:
    candidates = _candidates()
    good = SemanticResolution(metric_ids=[M_REVENUE], dimension_ids=[D_STATUS])
    assert resolution_problems(good, candidates) == []
    bad = SemanticResolution(metric_ids=[M_HIDDEN])
    assert resolution_problems(bad, candidates) == [f"unknown metric id {M_HIDDEN}"]


def test_plan_must_measure_the_metric_as_defined() -> None:
    candidates = _candidates()
    metric, dimension = candidates.metrics[0], candidates.dimensions[0]
    right = QueryPlan(
        tables=["sales.orders"],
        measures=[metric_measure(metric)],
        dimensions=["sales.orders.status"],
    )
    assert semantic_plan_problems(right, [metric], [dimension]) == []
    wrong = QueryPlan(
        tables=["sales.orders"],
        measures=[PlanMeasure(column="sales.orders.amount", aggregation="avg", alias="revenue")],
    )
    assert semantic_plan_problems(wrong, [metric], [dimension]) == [
        "metric Net Revenue (USD) must be measured as sum(sales.orders.amount) alias net_revenue_usd",
        "dimension Status must group by sales.orders.status",
    ]


@pytest.mark.parametrize(
    ("sql", "ok"),
    [
        ('SELECT SUM("t"."amount") AS "revenue" FROM "sales"."orders" AS "t"', True),
        ("select sum( t.amount ) from sales.orders t", True),
        ("SELECT AVG(t.amount) FROM sales.orders t", False),
        ("SELECT SUM(t.amount_net) FROM sales.orders t", False),
        ("SELECT SUM(t.amount) * 2 FROM sales.orders t", True),
    ],
)
def test_sql_must_aggregate_the_metric(sql: str, ok: bool) -> None:
    metric = _candidates().metrics[0]
    assert (sql_metric_problems(sql, [metric]) == []) is ok


SCHEMA = [
    ResultField(field="month", type="temporal"),
    ResultField(field="revenue", type="quantitative"),
    ResultField(field="region", type="nominal"),
]
ROWS = [
    ["2026-04-01T00:00:00", "1200.50", "West"],
    ["2026-05-01T00:00:00", "980", "East"],
    ["2026-06-01T00:00:00", None, "West"],
]


def test_stats_never_carry_rows_or_categorical_values() -> None:
    stats = result_stats(SCHEMA, ROWS, row_count=3, truncated=False)
    by_field = {c.field: c for c in stats.columns}
    assert (by_field["revenue"].sum, by_field["revenue"].count) == (2180.5, 2)
    assert (by_field["revenue"].min, by_field["revenue"].max, by_field["revenue"].mean) == (
        980.0,
        1200.5,
        1090.25,
    )
    assert by_field["month"].min == "2026-04-01T00:00:00" and by_field["month"].max.startswith(
        "2026-06"
    )  # type: ignore[union-attr]
    assert by_field["region"].distinct_count == 2
    assert "West" not in stats.model_dump_json()


@pytest.mark.parametrize(
    ("headline", "grounded"),
    [
        ("Q2 revenue: 2,180.50 across 3 months", True),
        ("Revenue peaked at 1,200.5 in April 2026", True),
        ("Average month: 1090.25", True),
        ("Revenue totalled 2,181", True),  # rounds from 2180.5 at the precision written
        ("Revenue grew 22.5% from April", False),
        ("Revenue reached 5,000", False),
    ],
)
def test_every_number_in_an_insight_is_grounded(headline: str, grounded: bool) -> None:
    stats = result_stats(SCHEMA, ROWS, row_count=3, truncated=False)
    problems = insight_problems(ResultInsight(headline=headline), stats, "revenue for Q2 2026")
    assert (problems == []) is grounded, problems
