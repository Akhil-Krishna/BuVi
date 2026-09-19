"""Analytics run wire contracts (Sections 11, 18.1).

`AnalyticsRunEvent` is the only thing a user ever sees of a run's progress: a stage, a status and
a user-safe message -- never model reasoning. Event payloads carry a `schema_version`; a consumer
rejects an unknown major version rather than guessing (Section 18.1).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Final, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

RunStage = Literal[
    "intent",
    "schema",
    "semantic",
    "sql",
    "validation",
    "execution",
    "visualization",
    "artifact",
    "run",
]
RUN_STAGES: Final[tuple[str, ...]] = get_args(RunStage)
SUPPORTED_MAJOR: Final = "1"


class SchemaVersionError(ValueError):
    """An event with a schema major version this consumer does not understand."""


class _Versioned(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: str = Field(default="1.0", pattern=r"^\d+\.\d+$")

    @classmethod
    def parse_event(cls, data: bytes | str | dict[str, Any]) -> Any:
        model = (
            cls.model_validate_json(data)
            if isinstance(data, bytes | str)
            else cls.model_validate(data)
        )
        if model.schema_version.split(".", 1)[0] != SUPPORTED_MAJOR:
            raise SchemaVersionError(model.schema_version)
        return model


class RunRequested(_Versioned):
    """`analytics.run.requested`: analytics-orchestrator -> worker-runtime."""

    run_id: uuid.UUID
    tenant_id: uuid.UUID
    conversation_id: uuid.UUID
    request_id: str | None = Field(default=None, max_length=128)


UsageMetric = Literal["llm_input_tokens", "llm_output_tokens", "query_execution_ms"]
USAGE_METRICS: Final[tuple[str, ...]] = get_args(UsageMetric)


class BillingUsageRecorded(_Versioned):
    """`billing.usage.recorded`: one metered quantity (Section 23).

    1.1 (Phase A11): `event_id` (the aggregator's idempotency key, so a redelivered message is
    counted once), `occurred_at`, and `query_execution_ms` from query-gateway, which has no model.
    """

    schema_version: str = Field(default="1.1", pattern=r"^\d+\.\d+$")
    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    tenant_id: uuid.UUID
    metric: UsageMetric
    quantity: int = Field(ge=0)
    occurred_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.UTC))
    run_id: uuid.UUID | None = None
    stage: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=128)
    request_id: str | None = Field(default=None, max_length=128)


class AnalyticsRunEvent(BaseModel):
    """Section 11. Serialized camelCase; SSE `id` is `seq`, SSE `event` is `<stage>.<status>`."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, alias_generator=to_camel, populate_by_name=True
    )

    run_id: str
    seq: int = Field(ge=1)
    stage: RunStage
    status: Literal["started", "completed", "failed"]
    message: str = Field(max_length=300)
    artifact_id: str | None = None
    created_at: dt.datetime

    @property
    def event_name(self) -> str:
        return f"{self.stage}.{self.status}"

    @property
    def is_terminal(self) -> bool:
        return self.stage == "run"

    def to_wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)
