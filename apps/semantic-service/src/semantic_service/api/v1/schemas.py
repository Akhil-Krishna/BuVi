"""Request/response schemas (Sections 8.3, 9)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

_TEXT = r"^[^<>{}`\x00-\x1f]*$"
Name = Annotated[str, Field(min_length=1, max_length=80, pattern=_TEXT)]
Synonym = Annotated[str, Field(min_length=1, max_length=60, pattern=_TEXT)]
Grain = Literal["day", "week", "month", "quarter", "year"]


class HealthResponse(BaseModel):
    status: str
    service: str
    checks: dict[str, str] = Field(default_factory=dict)


class MetricCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    #: Section 8.3 v1 grammar: AGG([DISTINCT] column) over the base table.
    expression: Annotated[str, Field(min_length=1, max_length=200)]
    base_table_id: uuid.UUID
    description: Annotated[str, Field(max_length=500, pattern=_TEXT)] | None = None
    default_grain: Grain | None = None
    synonyms: Annotated[list[Synonym], Field(max_length=20)] = Field(default_factory=list)


class MetricResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    expression: str
    default_grain: str | None
    base_table_id: uuid.UUID
    synonyms: list[str]
    status: str
    created_by: uuid.UUID
    approved_by: uuid.UUID | None
    approved_at: dt.datetime | None
    created_at: dt.datetime


class MetricListResponse(BaseModel):
    items: list[MetricResponse]
    next_cursor: str | None


class DimensionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    column_id: uuid.UUID
    synonyms: Annotated[list[Synonym], Field(max_length=20)] = Field(default_factory=list)


class DimensionResponse(BaseModel):
    id: uuid.UUID
    name: str
    column_id: uuid.UUID
    synonyms: list[str]
    created_by: uuid.UUID
    created_at: dt.datetime


class DimensionListResponse(BaseModel):
    items: list[DimensionResponse]
    next_cursor: str | None


class ContextMetric(BaseModel):
    """What the Flow needs of an approved metric; the aggregation and column are parsed here so
    the orchestrator never parses an expression itself."""

    id: uuid.UUID
    name: str
    description: str | None
    synonyms: list[str]
    aggregation: str
    column: str
    base_table_id: uuid.UUID
    default_grain: str | None


class ContextDimension(BaseModel):
    id: uuid.UUID
    name: str
    synonyms: list[str]
    column_id: uuid.UUID


class SemanticContextResponse(BaseModel):
    tenant_id: uuid.UUID
    metrics: list[ContextMetric]
    dimensions: list[ContextDimension]
