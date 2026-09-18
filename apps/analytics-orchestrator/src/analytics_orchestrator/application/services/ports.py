"""What the Flow needs from the outside world, as protocols (Section 4.1: application depends on
interfaces, infrastructure implements them). Every failure is a typed exception -- upstream error
text never flows back into a run."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from analytics_orchestrator.domain.value_objects.run_state import ContextTable, ExecutionSummary
from platform_auth import Principal
from platform_contracts import AnalyticsRunEvent, BillingUsageRecorded, RunRequested

OutputT = TypeVar("OutputT", bound=BaseModel)


# --- Models -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderResponse[T]:
    output: T
    input_tokens: int
    output_tokens: int
    model: str


class ProviderError(Exception):
    def __init__(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        super().__init__(type(self).__name__)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class ProviderUnavailableError(ProviderError):
    """Transient: rate limit, 5xx, connection, timeout. The router may fall back."""


class ProviderRefusedError(ProviderError):
    """The model declined (stop_reason `refusal`). The router may fall back."""


class ProviderOutputInvalidError(ProviderError):
    """The model answered, but not in the requested schema. Counts as a repair attempt."""


class ModelProvider(Protocol):
    name: str

    async def generate(
        self,
        *,
        model: str,
        system: str,
        user: str,
        payload: Mapping[str, Any],
        output_type: type[OutputT],
        max_tokens: int,
    ) -> ProviderResponse[OutputT]: ...


class TokenLedger(Protocol):
    async def used_today(self, tenant_id: str) -> int: ...

    async def charge(self, tenant_id: str, tokens: int) -> None: ...


class UsageSink(Protocol):
    async def record(self, event: BillingUsageRecorded) -> None: ...


# --- Other services -------------------------------------------------------------------------


class DependencyUnavailableError(Exception):
    """An upstream service failed; the run fails with UPSTREAM_UNAVAILABLE."""


class DelegatedUserDeniedError(Exception):
    """The requesting user is unknown or no longer active."""


class DelegatedIdentity(Protocol):
    async def resolve(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> Principal: ...


@dataclass(frozen=True)
class ActiveSource:
    id: uuid.UUID
    name: str


@dataclass(frozen=True)
class ContextSnapshot:
    status: str
    tables: list[ContextTable]
    #: The data source's engine; decides the SQL dialect (Phase A8).
    engine: str = "postgres"


class DataSourceUnknownError(Exception):
    """The data source does not exist in this tenant."""


class MetadataContext(Protocol):
    async def active_data_sources(self, tenant_id: uuid.UUID) -> list[ActiveSource]: ...

    async def context(self, tenant_id: uuid.UUID, data_source_id: uuid.UUID) -> ContextSnapshot: ...


@dataclass(frozen=True)
class ValidatedSql:
    query_id: str
    sql: str
    tables: list[str]


class QueryRejectedError(Exception):
    def __init__(self, reason: str, detail: str | None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


class QueryDeniedError(Exception):
    """403/404 from query-gateway: not this user's data source, or not permitted."""


class QueryNotActiveError(Exception):
    pass


class QueryTimedOutError(Exception):
    pass


class QueryExecutionError(Exception):
    pass


@dataclass(frozen=True)
class QueryCall:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    run_id: uuid.UUID
    data_source_id: uuid.UUID
    sql: str


class QueryGateway(Protocol):
    async def validate(self, call: QueryCall) -> ValidatedSql: ...

    async def execute(
        self, call: QueryCall, *, max_rows: int, timeout_ms: int
    ) -> ExecutionSummary: ...


# --- Semantic layer (Phase A7) -------------------------------------------------------------------


@dataclass(frozen=True)
class SemanticSnapshot:
    """Approved metrics (aggregation + bare column + base table id) and dimensions, as served."""

    metrics: list[dict[str, Any]]
    dimensions: list[dict[str, Any]]


class SemanticCatalog(Protocol):
    async def context(self, tenant_id: uuid.UUID) -> SemanticSnapshot: ...


# --- Visualization and artifacts (Phase A6) ------------------------------------------------------


@dataclass(frozen=True)
class ChartCheck:
    valid: bool
    chart_spec: dict[str, Any] | None
    problems: list[str]


class ChartValidator(Protocol):
    """visualization-service: the only ChartSpec validator (Section 17)."""

    async def check(
        self, chart_spec: dict[str, Any], result_schema: list[dict[str, Any]]
    ) -> ChartCheck: ...


@dataclass(frozen=True)
class ArtifactDraft:
    artifact_id: uuid.UUID
    tenant_id: uuid.UUID
    conversation_id: uuid.UUID
    run_id: uuid.UUID
    title: str
    summary: str
    semantic_query: dict[str, Any]
    source_refs: list[dict[str, Any]]
    validated_sql: str
    query_result_ref: str
    result_schema: list[dict[str, Any]]
    chart_spec: dict[str, Any]
    created_by: uuid.UUID


class ArtifactRejectedError(Exception):
    """dashboard-service refused the artifact (invalid chart spec or an id conflict)."""


class ArtifactStore(Protocol):
    """dashboard-service: the canonical artifact store (Section 8.9). Idempotent on the id."""

    async def store(self, draft: ArtifactDraft) -> None: ...


# --- Events and queue -------------------------------------------------------------------------


class RunEventPublisher(Protocol):
    async def publish(self, event: AnalyticsRunEvent) -> None: ...


class RunQueue(Protocol):
    async def enqueue(self, message: RunRequested) -> None: ...


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)
