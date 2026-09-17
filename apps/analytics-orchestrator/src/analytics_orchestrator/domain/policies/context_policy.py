"""Deterministic context rules: which tables reach a prompt, whether a plan stays inside them,
and how result columns map onto ChartSpec field types (Sections 12, 17)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

from analytics_orchestrator.domain.value_objects.agent_outputs import AnalyticsRequest, QueryPlan
from analytics_orchestrator.domain.value_objects.run_state import ContextTable
from platform_contracts.chart_spec import FieldType

_WORD: Final = re.compile(r"[a-z0-9]+")
_SYNONYMS: Final[dict[str, tuple[str, ...]]] = {
    "revenue": ("amount", "revenue", "total", "price", "sales", "order"),
    "sales": ("order", "amount", "sales", "revenue"),
    "region": ("region", "regions", "country", "territory"),
    "customer": ("customer", "customers", "client"),
    "product": ("product", "products", "item"),
}
_TEMPORAL: Final = ("date", "time", "timestamp", "interval")
_NUMERIC: Final = ("int", "numeric", "decimal", "real", "double", "float", "money", "serial")


def request_terms(request: AnalyticsRequest | None, message: str) -> list[str]:
    words = set(_WORD.findall(message.lower()))
    if request is not None:
        for text in (request.title, *request.metrics, *request.dimensions):
            words |= set(_WORD.findall(text.lower()))
    expanded = set(words)
    for word in words:
        expanded.update(_SYNONYMS.get(word, ()))
    return sorted(expanded)


def rank_tables(
    tables: Sequence[ContextTable], terms: Sequence[str], limit: int
) -> list[ContextTable]:
    """Lexical relevance over names and descriptions (Section 12, first slice)."""
    term_set = set(terms)

    def score(table: ContextTable) -> int:
        names = set(_WORD.findall(f"{table.table_name} {table.description or ''}".lower()))
        columns = {w for c in table.columns for w in _WORD.findall(c.name.lower())}
        return 3 * len(names & term_set) + len(columns & term_set)

    ranked = sorted(tables, key=lambda t: (-score(t), t.qualified_name))
    relevant = [t for t in ranked if score(t) > 0]
    return (relevant or ranked)[:limit]


def plan_problems(plan: QueryPlan, tables: Sequence[ContextTable]) -> list[str]:
    """References outside the context packet. Reported to the model by identifier only."""
    known_tables = {t.qualified_name: t for t in tables}
    problems = [f"unknown table {name}" for name in plan.tables if name not in known_tables]
    columns = (
        [m.column for m in plan.measures] + [f.column for f in plan.filters] + list(plan.dimensions)
    )
    if plan.time_column:
        columns.append(plan.time_column)
    for column in columns:
        schema, table, name = [*column.split("."), "", "", ""][:3]
        context = known_tables.get(f"{schema}.{table}")
        if context is None or name not in {c.name for c in context.columns}:
            problems.append(f"unknown column {column}")
        elif f"{schema}.{table}" not in plan.tables:
            problems.append(f"column {column} is not from a planned table")
    return problems


def field_type_for(postgres_type: str) -> FieldType:
    lowered = postgres_type.lower()
    if any(token in lowered for token in _TEMPORAL):
        return "temporal"
    if any(token in lowered for token in _NUMERIC):
        return "quantitative"
    return "nominal"
