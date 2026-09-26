"""Typed outputs of the LLM stages (Section 10.3: "every hop is a typed, schema-validated model").

Bounded everywhere: enums instead of free strings, identifier patterns instead of prose, length
caps on every list and string. Nothing an agent produces reaches the next stage untyped.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_STRICT = ConfigDict(extra="forbid", frozen=True)
_IDENT: Final = r"^[A-Za-z_][A-Za-z0-9_]{0,62}$"
_QUALIFIED_TABLE: Final = r"^[A-Za-z_][A-Za-z0-9_]{0,62}\.[A-Za-z_][A-Za-z0-9_]{0,62}$"
_QUALIFIED_COLUMN: Final = (
    r"^[A-Za-z_][A-Za-z0-9_]{0,62}\.[A-Za-z_][A-Za-z0-9_]{0,62}\.[A-Za-z_][A-Za-z0-9_]{0,62}$"
)
_TEXT: Final = r"^[^<>{}`\x00-\x1f]*$"
_UUID: Final = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"

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
    #: `conversation`: not a data question -- a greeting, thanks, "what can you do", or a request
    #: the product cannot help with. The run ends successfully with `reply` and never touches a
    #: data source. `unsupported` is what remains when even a reply cannot be written.
    intent: Literal["visualization", "question", "conversation", "unsupported"]
    title: str = Field(min_length=1, max_length=120, pattern=_TEXT)
    metrics: list[str] = Field(default_factory=list, max_length=5)
    dimensions: list[str] = Field(default_factory=list, max_length=5)
    time_range: TimeRange = Field(default_factory=TimeRange)
    chart_preference: ChartPreference | None = None
    #: Shown to the user verbatim as the run's closing message, so it is bounded and plain text
    #: (`_TEXT` forbids markup and control characters; 300 is `AnalyticsRunEvent.message`'s limit).
    reply: str | None = Field(default=None, max_length=300, pattern=_TEXT)

    @model_validator(mode="after")
    def _a_conversation_needs_its_reply(self) -> AnalyticsRequest:
        if self.intent == "conversation" and not (self.reply and self.reply.strip()):
            raise ValueError("a conversation intent must carry a reply")
        return self


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


class SemanticResolution(BaseModel):
    """resolve_semantics: which approved metrics/dimensions the request's business terms mean.
    Ids only, checked against the candidates the model was shown."""

    model_config = _STRICT
    metric_ids: list[Annotated[str, Field(pattern=_UUID)]] = Field(
        default_factory=list, max_length=5
    )
    dimension_ids: list[Annotated[str, Field(pattern=_UUID)]] = Field(
        default_factory=list, max_length=3
    )
    unmatched_terms: list[Annotated[str, Field(min_length=1, max_length=60, pattern=_TEXT)]] = (
        Field(default_factory=list, max_length=5)
    )


class ResultInsight(BaseModel):
    """analyze_result (ADR 0009): a short, plain-text reading of aggregate statistics. Every
    number in it must be grounded in those statistics or the user's request."""

    model_config = _STRICT
    headline: str = Field(min_length=1, max_length=200, pattern=_TEXT)
    observations: list[Annotated[str, Field(min_length=1, max_length=160, pattern=_TEXT)]] = Field(
        default_factory=list, max_length=3
    )
