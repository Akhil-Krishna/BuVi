"""`analytics.runs.flow_state` -- the CrewAI Flow's persisted state (Sections 8.4, 10.2, 16).

Persisted after every step, so a restarted executor resumes at the first step not in
`completed_steps` and never re-emits an event in `emitted_events`. It holds references and
shapes, never result rows: the executed result lives behind query-gateway's TTL-bound handle,
and the artifact itself in dashboard-service (Phase A6).
"""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, Field

from analytics_orchestrator.domain.value_objects.agent_outputs import AnalyticsRequest, QueryPlan
from platform_contracts import ChartSpec, ResultField


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class ContextColumn(BaseModel):
    name: str
    data_type: str
    description: str | None = None


class ContextTable(BaseModel):
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


class ExecutionSummary(BaseModel):
    query_id: str
    result_schema: list[ResultField]
    row_count: int
    truncated: bool
    result_handle: str
    result_expires_at: dt.datetime


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
