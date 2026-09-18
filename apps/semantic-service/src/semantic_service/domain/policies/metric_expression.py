"""Section 8.3 metric expressions (v1) and definition rules -- pure functions.

An expression is `AGG([DISTINCT] column)` over the metric's base table, never free SQL: it is
parsed here, checked against the catalog, stored normalized, and turned into a query-plan measure
deterministically by the Flow.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

Aggregation = Literal["sum", "avg", "min", "max", "count", "count_distinct"]
MetricStatus = Literal["draft", "approved", "deprecated"]

_EXPRESSION: Final = re.compile(
    r"^\s*(?P<agg>sum|avg|min|max|count)\s*\(\s*(?P<distinct>distinct\s+)?"
    r"(?:(?P<table>[a-z_][a-z0-9_]{0,62})\.)?(?P<column>[a-z_][a-z0-9_]{0,62})\s*\)\s*$",
    re.IGNORECASE,
)
_NUMERIC: Final = ("int", "numeric", "decimal", "real", "double", "float", "money", "serial")
_TRANSITIONS: Final[Mapping[MetricStatus, MetricStatus]] = {
    "draft": "approved",
    "approved": "deprecated",
}


class MetricExpressionError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedExpression:
    aggregation: Aggregation
    column: str
    table: str | None

    @property
    def normalized(self) -> str:
        if self.aggregation == "count_distinct":
            return f"COUNT(DISTINCT {self.column})"
        return f"{self.aggregation.upper()}({self.column})"


def parse_expression(expression: str) -> ParsedExpression:
    match = _EXPRESSION.match(expression)
    if match is None:
        raise MetricExpressionError("expression must be AGG([DISTINCT] column)")
    aggregation = match["agg"].lower()
    if match["distinct"] and aggregation != "count":
        raise MetricExpressionError("DISTINCT is only supported with COUNT")
    return ParsedExpression(
        aggregation="count_distinct" if match["distinct"] else aggregation,  # type: ignore[arg-type]
        column=match["column"].lower(),
        table=match["table"].lower() if match["table"] else None,
    )


@dataclass(frozen=True)
class CatalogColumnRef:
    name: str
    data_type: str
    is_pii: bool


@dataclass(frozen=True)
class CatalogTableRef:
    table_name: str
    is_visible_to_agent: bool
    columns: tuple[CatalogColumnRef, ...]


def metric_problems(parsed: ParsedExpression, table: CatalogTableRef | None) -> list[str]:
    """Why a parsed metric cannot be defined on `table` (None: not in this tenant's catalog)."""
    if table is None:
        return ["base_table_id: not in the catalog"]
    problems: list[str] = []
    if not table.is_visible_to_agent:
        problems.append("base_table_id: table is hidden from agents")
    if parsed.table is not None and parsed.table != table.table_name.lower():
        problems.append("expression: table qualifier is not the base table")
    column = next((c for c in table.columns if c.name.lower() == parsed.column), None)
    if column is None:
        problems.append("expression: column not in the base table")
    elif column.is_pii:
        problems.append("expression: column is PII")
    elif parsed.aggregation in ("sum", "avg") and not any(
        token in column.data_type.lower() for token in _NUMERIC
    ):
        problems.append("expression: SUM/AVG need a numeric column")
    return problems


def dimension_problems(column: CatalogColumnRef | None, table_visible: bool | None) -> list[str]:
    if column is None or table_visible is None:
        return ["column_id: not in the catalog"]
    problems = []
    if column.is_pii:
        problems.append("column_id: column is PII")
    if not table_visible:
        problems.append("column_id: table is hidden from agents")
    return problems


def next_status(current: MetricStatus, target: MetricStatus) -> bool:
    """Only draft -> approved -> deprecated."""
    return _TRANSITIONS.get(current) == target
