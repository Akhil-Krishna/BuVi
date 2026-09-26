"""Deterministic context rules: which tables reach a prompt, whether a plan stays inside them,
and how result columns map onto ChartSpec field types (Sections 12, 17)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

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


def _stem(word: str) -> str:
    """Fold a plural onto its singular so 'stores' and 'store' are the same word.

    Applied to *both* sides of every comparison (the request's terms and the catalog's words), so
    all that matters is that it is consistent, not that it is linguistically right: 'status' becomes
    'statu' on both sides and still matches itself. Without it the strongest signal there is -- a
    question saying "store" against a table called `stores` -- scored zero, and only an unrelated
    column word could win.
    """
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _words(text: str) -> set[str]:
    return {_stem(w) for w in _WORD.findall(text.lower())}


def request_terms(request: AnalyticsRequest | None, message: str) -> list[str]:
    words = set(_WORD.findall(message.lower()))
    if request is not None:
        for text in (request.title, *request.metrics, *request.dimensions):
            words |= set(_WORD.findall(text.lower()))
    expanded = set(words)
    for word in words:  # synonyms are keyed by the surface word, so expand before stemming
        expanded.update(_SYNONYMS.get(word, ()))
    return sorted({_stem(w) for w in expanded})


def table_score(table: ContextTable, term_set: set[str]) -> int:
    """Lexical relevance of one table: a name/description word is worth three column words."""
    names = _words(f"{table.table_name} {table.description or ''}")
    columns = {w for c in table.columns for w in _words(c.name)}
    return 3 * len(names & term_set) + len(columns & term_set)


def rank_tables(
    tables: Sequence[ContextTable], terms: Sequence[str], limit: int
) -> list[ContextTable]:
    """Lexical relevance over names and descriptions (Section 12, first slice)."""
    term_set = set(terms)
    ranked = sorted(tables, key=lambda t: (-table_score(t, term_set), t.qualified_name))
    relevant = [t for t in ranked if table_score(t, term_set) > 0]
    return (relevant or ranked)[:limit]


#: How far ahead the best data source must be before a run picks it without asking. A table-name
#: hit is worth 3 and a column hit 1 (`table_score`), so 2 means "a name-level advantage, or more
#: than one extra column". Deliberately not 1: a single stray column word is not evidence that a
#: question is about one database rather than another.
SOURCE_MARGIN: Final = 2

#: Never route across more sources than this: each candidate costs a metadata-service call.
MAX_ROUTED_SOURCES: Final = 10


@dataclass(frozen=True)
class SourceChoice:
    """Outcome of `choose_data_source`: exactly one of `source_id` / `reason` is set."""

    source_id: str | None
    reason: Literal["ambiguous", "no_match"] | None = None


def choose_data_source(
    candidates: Sequence[tuple[str, Sequence[ContextTable]]], terms: Sequence[str]
) -> SourceChoice:
    """Pick the data source a question is about, or say why it cannot.

    A source scores as its single best table (`table_score`) -- the question is about the source
    that has the table it is about, not the one with the most tables. The winner must lead the
    runner-up by `SOURCE_MARGIN`; a tie, or a narrow lead, is `ambiguous` and the run asks.

    The bias is deliberate. A wrong guess silently answers from the wrong database, and the chart
    looks perfectly plausible; a wrong refusal costs one click. So when the evidence is not clear,
    this asks. Nothing matching anywhere is reported separately (`no_match`) because "none of your
    data sources has anything like that" is a different message from "which of these two".
    """
    term_set = set(terms)
    scored = sorted(
        (
            (max((table_score(t, term_set) for t in tables), default=0), sid)
            for sid, tables in candidates
        ),
        reverse=True,
    )
    if not scored:
        return SourceChoice(None, "no_match")
    if len(scored) == 1:
        # Nothing to choose between. Same as a tenant with one source: `rank_tables` falls back to
        # every table rather than refusing, and the model still gets to plan.
        return SourceChoice(scored[0][1])
    if scored[0][0] == 0:
        return SourceChoice(None, "no_match")
    if scored[0][0] - scored[1][0] >= SOURCE_MARGIN:
        return SourceChoice(scored[0][1])
    return SourceChoice(None, "ambiguous")


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
