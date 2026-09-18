"""Section 8.3 metric expression grammar (v1) and definition rules."""

from __future__ import annotations

import pytest

from semantic_service.domain.policies.metric_expression import (
    CatalogColumnRef,
    CatalogTableRef,
    MetricExpressionError,
    dimension_problems,
    metric_problems,
    next_status,
    parse_expression,
)

pytestmark = pytest.mark.unit

ORDERS = CatalogTableRef(
    table_name="orders",
    is_visible_to_agent=True,
    columns=(
        CatalogColumnRef("id", "integer", False),
        CatalogColumnRef("amount", "numeric(12,2)", False),
        CatalogColumnRef("status", "text", False),
        CatalogColumnRef("email", "text", True),
    ),
)


@pytest.mark.parametrize(
    ("expression", "aggregation", "column", "normalized"),
    [
        ("SUM(amount)", "sum", "amount", "SUM(amount)"),
        ("sum( orders.amount )", "sum", "amount", "SUM(amount)"),
        ("COUNT(DISTINCT id)", "count_distinct", "id", "COUNT(DISTINCT id)"),
        ("avg(amount)", "avg", "amount", "AVG(amount)"),
        ("Max(Amount)", "max", "amount", "MAX(amount)"),
    ],
)
def test_valid_expressions_normalize(
    expression: str, aggregation: str, column: str, normalized: str
) -> None:
    parsed = parse_expression(expression)
    assert (parsed.aggregation, parsed.column, parsed.normalized) == (
        aggregation,
        column,
        normalized,
    )


@pytest.mark.parametrize(
    "expression",
    [
        "amount",
        "SUM(amount) + 1",
        "SUM(amount); DROP TABLE orders",
        "SUM(CASE WHEN status = 'x' THEN amount END)",
        "SUM(DISTINCT amount)",
        "COUNT(*)",
        "pg_sleep(10)",
        "SUM(amount) FILTER (WHERE true)",
        "SUM(sales.orders.amount)",
        "SUM(amount -- comment\n)",
    ],
)
def test_anything_but_a_single_aggregate_is_rejected(expression: str) -> None:
    with pytest.raises(MetricExpressionError):
        parse_expression(expression)


def test_catalog_rules() -> None:
    assert metric_problems(parse_expression("SUM(amount)"), ORDERS) == []
    assert metric_problems(parse_expression("SUM(amount)"), None) == [
        "base_table_id: not in the catalog"
    ]
    assert metric_problems(parse_expression("COUNT(email)"), ORDERS) == [
        "expression: column is PII"
    ]
    assert metric_problems(parse_expression("SUM(status)"), ORDERS) == [
        "expression: SUM/AVG need a numeric column"
    ]
    assert metric_problems(parse_expression("SUM(profit)"), ORDERS) == [
        "expression: column not in the base table"
    ]
    assert metric_problems(parse_expression("SUM(items.amount)"), ORDERS) == [
        "expression: table qualifier is not the base table"
    ]
    hidden = CatalogTableRef("orders", False, ORDERS.columns)
    assert "base_table_id: table is hidden from agents" in metric_problems(
        parse_expression("SUM(amount)"), hidden
    )
    assert metric_problems(parse_expression("COUNT(status)"), ORDERS) == []


def test_dimension_rules() -> None:
    assert dimension_problems(CatalogColumnRef("status", "text", False), True) == []
    assert dimension_problems(CatalogColumnRef("email", "text", True), True) == [
        "column_id: column is PII"
    ]
    assert dimension_problems(None, None) == ["column_id: not in the catalog"]
    assert dimension_problems(CatalogColumnRef("status", "text", False), False) == [
        "column_id: table is hidden from agents"
    ]


def test_status_transitions() -> None:
    assert next_status("draft", "approved") and next_status("approved", "deprecated")
    assert not next_status("draft", "deprecated")
    assert not next_status("deprecated", "approved")
    assert not next_status("approved", "approved")
