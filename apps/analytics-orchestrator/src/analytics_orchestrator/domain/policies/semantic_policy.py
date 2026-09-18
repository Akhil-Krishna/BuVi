"""Semantic grounding rules (Sections 8.3, 12; Phase A7) -- pure functions.

An approved metric becomes a query-plan measure deterministically: its aggregation and column
come from the definition, never from the model. The plan and the validated SQL are checked to
use it; the model only chooses *which* approved metric a business term means.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Final

import sqlglot
from sqlglot import exp

from analytics_orchestrator.domain.value_objects.agent_outputs import (
    PlanMeasure,
    QueryPlan,
    SemanticResolution,
)
from analytics_orchestrator.domain.value_objects.run_state import (
    ContextTable,
    SemanticDimension,
    SemanticMetric,
)

_AGGREGATIONS = frozenset({"sum", "avg", "min", "max", "count", "count_distinct"})


@dataclass(frozen=True)
class Candidates:
    metrics: list[SemanticMetric]
    dimensions: list[SemanticDimension]
    #: Base tables of candidate metrics, by qualified name.
    tables: dict[str, ContextTable]


def semantic_candidates(
    metrics: Iterable[dict[str, Any]],
    dimensions: Iterable[dict[str, Any]],
    permitted_tables: Sequence[ContextTable],
) -> Candidates:
    """Keep only definitions whose base table / column is in the permitted context packet
    (agent-visible table, non-PII column): a metric cannot widen what the agent may query."""
    by_table_id = {t.id: t for t in permitted_tables if t.id}
    by_column_id = {c.id: (t, c) for t in permitted_tables for c in t.columns if c.id}
    kept_metrics: list[SemanticMetric] = []
    tables: dict[str, ContextTable] = {}
    for metric in metrics:
        table = by_table_id.get(str(metric["base_table_id"]))
        if table is None or metric.get("aggregation") not in _AGGREGATIONS:
            continue
        if str(metric["column"]) not in {c.name for c in table.columns}:
            continue
        kept_metrics.append(
            SemanticMetric(
                id=str(metric["id"]),
                name=str(metric["name"]),
                description=metric.get("description"),
                synonyms=[str(s) for s in metric.get("synonyms", [])],
                aggregation=str(metric["aggregation"]),
                column=f"{table.qualified_name}.{metric['column']}",
                default_grain=metric.get("default_grain"),
            )
        )
        tables[table.qualified_name] = table
    kept_dimensions = []
    for dimension in dimensions:
        found = by_column_id.get(str(dimension["column_id"]))
        if found is None:
            continue
        table, column = found
        kept_dimensions.append(
            SemanticDimension(
                id=str(dimension["id"]),
                name=str(dimension["name"]),
                synonyms=[str(s) for s in dimension.get("synonyms", [])],
                column=f"{table.qualified_name}.{column.name}",
            )
        )
        tables[table.qualified_name] = table
    return Candidates(kept_metrics, kept_dimensions, tables)


def resolution_problems(resolution: SemanticResolution, candidates: Candidates) -> list[str]:
    metric_ids = {m.id for m in candidates.metrics}
    dimension_ids = {d.id for d in candidates.dimensions}
    problems = [f"unknown metric id {i}" for i in resolution.metric_ids if i not in metric_ids]
    problems += [
        f"unknown dimension id {i}" for i in resolution.dimension_ids if i not in dimension_ids
    ]
    return problems


def metric_measure(metric: SemanticMetric) -> PlanMeasure:
    return PlanMeasure(
        column=metric.column,
        aggregation=metric.aggregation,  # type: ignore[arg-type]
        alias=metric.alias,
    )


def semantic_plan_problems(
    plan: QueryPlan, metrics: Sequence[SemanticMetric], dimensions: Sequence[SemanticDimension]
) -> list[str]:
    measures = {(m.column, m.aggregation, m.alias) for m in plan.measures}
    problems = []
    for metric in metrics:
        required = metric_measure(metric)
        if (required.column, required.aggregation, required.alias) not in measures:
            problems.append(
                f"metric {metric.name} must be measured as {required.aggregation}"
                f"({required.column}) alias {required.alias}"
            )
    for dimension in dimensions:
        if dimension.column not in plan.dimensions:
            problems.append(f"dimension {dimension.name} must group by {dimension.column}")
    return problems


_AGGREGATE_NODES: Final[dict[str, type[exp.Expression]]] = {
    "sum": exp.Sum,
    "avg": exp.Avg,
    "min": exp.Min,
    "max": exp.Max,
    "count": exp.Count,
    "count_distinct": exp.Count,
}


def _tables_by_alias(select: exp.Select) -> dict[str, str]:
    tables: dict[str, str] = {}
    for table in select.find_all(exp.Table):
        qualified = f"{table.db}.{table.name}".lower() if table.db else table.name.lower()
        tables[(table.alias or table.name).lower()] = qualified
    return tables


def _is_exact_aggregate(
    node: exp.Expression, metric: SemanticMetric, tables: dict[str, str]
) -> bool:
    """True only for `AGG([DISTINCT] base_table.column)` itself -- no arithmetic, FILTER, window
    or CASE around or inside it."""
    expected = _AGGREGATE_NODES[metric.aggregation]
    if type(node) is not expected:
        return False
    argument = node.this
    if metric.aggregation == "count_distinct":
        if not isinstance(argument, exp.Distinct) or len(argument.expressions) != 1:
            return False
        argument = argument.expressions[0]
    elif isinstance(argument, exp.Distinct):
        return False
    if not isinstance(argument, exp.Column):
        return False
    schema, table, column = metric.column.lower().split(".")
    if argument.name.lower() != column:
        return False
    qualifier = argument.table.lower()
    if qualifier:
        resolved = tables.get(qualifier)
    else:
        resolved = next(iter(tables.values())) if len(tables) == 1 else None
    return resolved in (f"{schema}.{table}", table)


def sql_metric_problems(
    sql: str, metrics: Sequence[SemanticMetric], *, dialect: str = "postgres"
) -> list[str]:
    """The validated (regenerated) SQL must output each resolved metric, under its alias, as
    exactly the defined aggregate. Anything unparseable or ambiguous is a problem (fail closed)."""
    if not metrics:
        return []
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.ParseError:
        return ["SQL could not be parsed to check the metrics it must compute"]
    if not isinstance(tree, exp.Select):
        return ["SQL must be a single SELECT to compute the defined metrics"]
    tables = _tables_by_alias(tree)
    outputs: dict[str, list[exp.Expression]] = {}
    for item in tree.expressions:
        outputs.setdefault(item.alias_or_name.lower(), []).append(
            item.this if isinstance(item, exp.Alias) else item
        )
    problems = []
    for metric in metrics:
        measure = metric_measure(metric)
        candidates = outputs.get(measure.alias, [])
        if len(candidates) != 1 or not _is_exact_aggregate(candidates[0], metric, tables):
            problems.append(
                f"SQL must output {measure.alias} as exactly {measure.aggregation}({metric.column})"
                f" for metric {metric.name}"
            )
    return problems
