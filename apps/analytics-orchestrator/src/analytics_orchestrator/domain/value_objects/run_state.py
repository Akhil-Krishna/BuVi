"""`analytics.runs.flow_state` -- the CrewAI Flow's persisted state (Sections 8.4, 10.2, 16).

Persisted after every step, so a restarted executor resumes at the first step not in
`completed_steps` and never re-emits an event in `emitted_events`. It holds references and
shapes, never result rows: the executed result lives behind query-gateway's TTL-bound handle,
and the artifact itself in dashboard-service (Phase A6).
"""

from __future__ import annotations

import datetime as dt
import re
import uuid

from pydantic import BaseModel, Field

from analytics_orchestrator.domain.value_objects.agent_outputs import (
    AnalyticsRequest,
    QueryPlan,
    ResultInsight,
)
from platform_contracts import ChartSpec, ResultField


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class ContextColumn(BaseModel):
    #: metadata.columns.id (Phase A7: dimensions reference catalog ids).
    id: str | None = None
    name: str
    data_type: str
    description: str | None = None


class ContextTable(BaseModel):
    #: metadata.tables.id (Phase A7: metrics reference their base table by id).
    id: str | None = None
    schema_name: str
    table_name: str
    description: str | None = None
    row_count_estimate: int | None = None
    columns: list[ContextColumn] = Field(default_factory=list)

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"


class SchemaContext(BaseModel):
    data_source_id: str
    tables: list[ContextTable]
    #: postgres | mysql -- the dialect the SQL generator writes and the metric check parses.
    engine: str = "postgres"


class ColumnStats(BaseModel):
    """ADR 0009: aggregates only -- never a row, never a categorical value."""

    field: str
    type: str
    count: int
    min: float | str | None = None
    max: float | str | None = None
    sum: float | None = None
    mean: float | None = None
    distinct_count: int | None = None


class ResultStats(BaseModel):
    row_count: int
    truncated: bool
    columns: list[ColumnStats]


class ExecutionSummary(BaseModel):
    query_id: str
    result_schema: list[ResultField]
    row_count: int
    truncated: bool
    result_handle: str
    result_expires_at: dt.datetime
    stats: ResultStats | None = None


class SemanticMetric(BaseModel):
    """An approved metric the agent may see, resolved onto the permitted context packet."""

    id: str
    name: str
    description: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    aggregation: str
    #: schema.table.column
    column: str
    default_grain: str | None = None

    @property
    def alias(self) -> str:
        slug = "_".join(re.findall(r"[a-z0-9]+", self.name.lower()))[:60] or "metric"
        return slug if slug[0].isalpha() else f"m_{slug}"


class SemanticDimension(BaseModel):
    id: str
    name: str
    synonyms: list[str] = Field(default_factory=list)
    column: str


class SemanticState(BaseModel):
    metrics: list[SemanticMetric] = Field(default_factory=list)
    dimensions: list[SemanticDimension] = Field(default_factory=list)
    unmatched_terms: list[str] = Field(default_factory=list)
    candidate_count: int = 0


class Grounding(BaseModel):
    """Per-run groundedness record (Phase A7, Section 25)."""

    metric_ids: list[str] = Field(default_factory=list)
    metric_names: list[str] = Field(default_factory=list)
    dimension_ids: list[str] = Field(default_factory=list)
    unmatched_terms: list[str] = Field(default_factory=list)
    measures_total: int = 0
    measures_from_metrics: int = 0
    insight_grounded: bool | None = None
    insight_fallback: bool = False


class AnalyticsRunState(BaseModel):
    #: CrewAI Flow state id: the run id.
    id: str
    tenant_id: str
    conversation_id: str
    requested_by: str
    message: str
    data_source_id: str | None = None
    deadline: dt.datetime | None = None
    completed_steps: list[str] = Field(default_factory=list)
    emitted_events: list[str] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    request: AnalyticsRequest | None = None
    schema_context: SchemaContext | None = None
    plan: QueryPlan | None = None
    generated_sql: str | None = None
    validated_sql: str | None = None
    validated_tables: list[str] = Field(default_factory=list)
    execution: ExecutionSummary | None = None
    semantic: SemanticState | None = None
    insight: ResultInsight | None = None
    grounding: Grounding = Field(default_factory=Grounding)
    chart_spec: ChartSpec | None = None
    #: The artifact stored in dashboard-service (Section 8.9); derived from the run id.
    artifact_id: str | None = None

    @classmethod
    def initial(
        cls,
        *,
        run_id: uuid.UUID,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID,
        requested_by: uuid.UUID,
        message: str,
        data_source_id: uuid.UUID | None,
    ) -> AnalyticsRunState:
        return cls(
            id=str(run_id),
            tenant_id=str(tenant_id),
            conversation_id=str(conversation_id),
            requested_by=str(requested_by),
            message=message,
            data_source_id=str(data_source_id) if data_source_id else None,
        )
