"""Typed outputs of the LLM stages (Section 10.3: "every hop is a typed, schema-validated model").

Bounded everywhere: enums instead of free strings, identifier patterns instead of prose, length
caps on every list and string. Nothing an agent produces reaches the next stage untyped.
"""

from __future__ import annotations

import datetime as dt
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

_STRICT = ConfigDict(extra="forbid", frozen=True)
_IDENT: Final = r"^[A-Za-z_][A-Za-z0-9_]{0,62}$"
_QUALIFIED_TABLE: Final = r"^[A-Za-z_][A-Za-z0-9_]{0,62}\.[A-Za-z_][A-Za-z0-9_]{0,62}$"
_QUALIFIED_COLUMN: Final = (
    r"^[A-Za-z_][A-Za-z0-9_]{0,62}\.[A-Za-z_][A-Za-z0-9_]{0,62}\.[A-Za-z_][A-Za-z0-9_]{0,62}$"
)
_TEXT: Final = r"^[^<>{}`\x00-\x1f]*$"

Grain = Literal["day", "week", "month", "quarter", "year"]
ChartPreference = Literal["line", "bar", "area", "scatter", "pie", "table"]


class TimeRange(BaseModel):
    model_config = _STRICT
    start: dt.date | None = None
    end: dt.date | None = None
    grain: Grain | None = None


class AnalyticsRequest(BaseModel):
    """classify_intent: free text -> a bounded request."""

    model_config = _STRICT
    intent: Literal["visualization", "question", "unsupported"]
    title: str = Field(min_length=1, max_length=120, pattern=_TEXT)
    metrics: list[str] = Field(default_factory=list, max_length=5)
    dimensions: list[str] = Field(default_factory=list, max_length=5)
    time_range: TimeRange = Field(default_factory=TimeRange)
    chart_preference: ChartPreference | None = None


class PlanMeasure(BaseModel):
    model_config = _STRICT
    column: str = Field(pattern=_QUALIFIED_COLUMN)
    aggregation: Literal["sum", "avg", "count", "min", "max", "count_distinct"]
    alias: str = Field(pattern=_IDENT)


class PlanFilter(BaseModel):
    model_config = _STRICT
    column: str = Field(pattern=_QUALIFIED_COLUMN)
    operator: Literal["=", "!=", ">", ">=", "<", "<=", "in", "between"]
    values: list[str] = Field(min_length=1, max_length=10)


class QueryPlan(BaseModel):
    """build_query_plan: a structured plan before any SQL (Section 10.1)."""

    model_config = _STRICT
    tables: list[str] = Field(min_length=1, max_length=3)
    measures: list[PlanMeasure] = Field(min_length=1, max_length=5)
    dimensions: list[str] = Field(default_factory=list, max_length=3)
    time_column: str | None = Field(default=None, pattern=_QUALIFIED_COLUMN)
    time_grain: Grain | None = None
    filters: list[PlanFilter] = Field(default_factory=list, max_length=5)
    limit: int = Field(default=1_000, ge=1, le=1_000)


class GeneratedSql(BaseModel):
    """generate_sql: SQL text only -- it is data for the validator, never executed by the Flow."""

    model_config = _STRICT
    sql: str = Field(min_length=1, max_length=20_000)
