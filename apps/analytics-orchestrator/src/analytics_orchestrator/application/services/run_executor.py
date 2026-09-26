"""Executes one AnalyticsRun (Sections 10, 10.2, 10.3, 11, 13, 16).

The CrewAI Flow (infrastructure/flow) owns the step sequence; every step delegates to
`RunExecutor.run_step`, which is where the rules live:

* a step already in `flow_state.completed_steps` is skipped -- resume never repeats work;
* the run's status is re-read before each step, so cancellation takes effect between steps;
* each step runs under the 90s stage timeout, inside the run's 300s deadline;
* after each step, `flow_state` is persisted; events are appended to `run_events` and published
  to Redis, each at most once (`emitted_events`);
* any failure ends the run with one `FailureCode`, a `<stage>.failed` event and `run.failed`.

A `BaseException` that is not an `Exception` (process death, task cancellation) is *not* a run
failure: it propagates, the run stays `running`, and the next execution resumes it.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import traceback
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analytics_orchestrator.application.services import prompts
from analytics_orchestrator.application.services.model_router import ModelRouter
from analytics_orchestrator.application.services.ports import (
    ArtifactDraft,
    ArtifactRejectedError,
    ArtifactStore,
    ChartValidator,
    ContextSnapshot,
    DataSourceUnknownError,
    DelegatedIdentity,
    DelegatedUserDeniedError,
    DependencyUnavailableError,
    MetadataContext,
    OutputT,
    QueryCall,
    QueryCapacityError,
    QueryDeniedError,
    QueryExecutionError,
    QueryGateway,
    QueryNotActiveError,
    QueryRejectedError,
    QueryTimedOutError,
    RunEventPublisher,
    SemanticCatalog,
    utcnow,
)
from analytics_orchestrator.domain.errors import NotFoundError, RunBusyError
from analytics_orchestrator.domain.policies.context_policy import (
    MAX_ROUTED_SOURCES,
    choose_data_source,
    plan_problems,
    rank_tables,
    request_terms,
)
from analytics_orchestrator.domain.policies.flow_steps import (
    FLOW_STEPS,
    STEP_EVENTS,
    TERMINAL_STATUSES,
)
from analytics_orchestrator.domain.policies.result_policy import insight_problems
from analytics_orchestrator.domain.policies.semantic_policy import (
    metric_measure,
    resolution_problems,
    semantic_candidates,
    semantic_plan_problems,
    sql_metric_problems,
)
from analytics_orchestrator.domain.value_objects.agent_outputs import (
    AnalyticsRequest,
    GeneratedSql,
    QueryPlan,
    ResultInsight,
    SemanticResolution,
)
from analytics_orchestrator.domain.value_objects.failures import (
    MESSAGES,
    FailureCode,
    RunFailedError,
)
from analytics_orchestrator.domain.value_objects.run_state import (
    AnalyticsRunState,
    ExecutionSummary,
    SchemaContext,
    SemanticState,
)
from analytics_orchestrator.infrastructure.db.models import Message
from analytics_orchestrator.infrastructure.db.repositories.analytics_repository import (
    AnalyticsRepository,
)
from analytics_orchestrator.infrastructure.db.session import tenant_scope
from platform_auth.permissions import PERM_CHAT_USE
from platform_contracts import AnalyticsRunEvent, ChartSpec

#: Artifact ids are derived from the run id, so a resumed run addresses the same artifact.
ARTIFACT_NAMESPACE = uuid.UUID("6f1f7c1e-3b0a-4d6e-9a0c-2f5d8e4b7a10")

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FlowLimits:
    stage_timeout_seconds: float
    run_timeout_seconds: float
    query_max_rows: int
    query_timeout_ms: int
    context_max_tables: int
    max_repairs: int
    #: Section 20: a tenant over its query concurrency cap is retried briefly, then the run
    #: fails QUERY_CONCURRENCY_LIMITED (never UPSTREAM_UNAVAILABLE).
    query_capacity_retries: int = 3
    query_capacity_backoff_seconds: float = 1.0


@dataclass(frozen=True)
class RunOutcome:
    run_id: uuid.UUID
    status: str
    error_code: str | None


class FlowRunner(Protocol):
    async def run(self, state: AnalyticsRunState, executor: RunExecutor) -> AnalyticsRunState: ...


class _AsyncLockContext(Protocol):
    async def __aenter__(self) -> bool: ...

    async def __aexit__(self, *args: object) -> None: ...


#: Test and operations hook, called after a step's state is persisted.
AfterStep = Callable[[str, AnalyticsRunState], Awaitable[None]]


class RunExecutor:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        lock: Callable[[uuid.UUID], _AsyncLockContext],
        flow: FlowRunner,
        router: ModelRouter,
        identity: DelegatedIdentity,
        metadata: MetadataContext,
        queries: QueryGateway,
        charts: ChartValidator,
        artifacts: ArtifactStore,
        semantics: SemanticCatalog,
        events: RunEventPublisher,
        limits: FlowLimits,
        after_step: AfterStep | None = None,
    ) -> None:
        self._sessions = session_factory
        self._lock = lock
        self._flow = flow
        self._router = router
        self._identity = identity
        self._metadata = metadata
        self._queries = queries
        self._charts = charts
        self._artifacts = artifacts
        self._semantics = semantics
        self._events = events
        self._limits = limits
        self._after_step = after_step
        # The state instance the Flow is running on, per run: CrewAI copies the state it is given.
        self._live: dict[str, AnalyticsRunState] = {}

    # --- run lifecycle ------------------------------------------------------------------

    async def execute(self, tenant_id: uuid.UUID, run_id: uuid.UUID) -> RunOutcome:
        async with self._lock(run_id) as acquired:
            if not acquired:
                raise RunBusyError()
            state = await self._begin(tenant_id, run_id)
            if state is None:
                return await self._outcome(tenant_id, run_id)
            logger.info(
                "run execution started",
                extra={
                    "context": {"run_id": state.id, "completed_steps": len(state.completed_steps)}
                },
            )
            try:
                remaining = (state.deadline - utcnow()).total_seconds() if state.deadline else 0
                if remaining <= 0:
                    raise RunFailedError(FailureCode.RUN_TIMEOUT)
                try:
                    async with asyncio.timeout(remaining):
                        state = await self._flow.run(state, self)
                except TimeoutError:
                    raise RunFailedError(FailureCode.RUN_TIMEOUT) from None
            except RunFailedError as failure:
                await self._fail(self._live.get(state.id, state), failure.code)
            except Exception as error:
                logger.error(
                    "run failed unexpectedly",
                    extra={"context": {"run_id": str(run_id), "error_type": type(error).__name__}},
                )
                await self._fail(self._live.get(state.id, state), FailureCode.INTERNAL_ERROR)
            finally:
                self._live.pop(state.id, None)
            outcome = await self._outcome(tenant_id, run_id)
            logger.info(
                "run execution finished",
                extra={"context": {"run_id": state.id, "status": outcome.status}},
            )
            return outcome

    async def _begin(self, tenant_id: uuid.UUID, run_id: uuid.UUID) -> AnalyticsRunState | None:
        async with tenant_scope(self._sessions, tenant_id) as db:
            repository = AnalyticsRepository(db)
            run = await repository.get_run(tenant_id, run_id, for_update=True)
            if run is None:
                raise NotFoundError()
            if run.status in TERMINAL_STATUSES:
                return None
            state = AnalyticsRunState.model_validate(run.flow_state)
            if state.deadline is None:
                state.deadline = utcnow() + dt.timedelta(seconds=self._limits.run_timeout_seconds)
            await repository.start_run(run, state.model_dump(mode="json"))
            await db.commit()
            return state

    async def _outcome(self, tenant_id: uuid.UUID, run_id: uuid.UUID) -> RunOutcome:
        async with tenant_scope(self._sessions, tenant_id) as db:
            run = await AnalyticsRepository(db).get_run(tenant_id, run_id)
            await db.commit()
        if run is None:
            raise NotFoundError()
        return RunOutcome(run_id=run.id, status=run.status, error_code=run.error_code)

    # --- steps ----------------------------------------------------------------------------

    async def run_step(self, state: AnalyticsRunState, step: str) -> None:
        self._live[state.id] = state
        if step in state.completed_steps:
            return
        await self._ensure_not_cancelled(state)
        events = STEP_EVENTS[step]
        if events.stage and events.started:
            await self._emit(state, events.stage, "started", events.started)
        handler = getattr(self, f"_{step}")
        try:
            async with asyncio.timeout(self._limits.stage_timeout_seconds):
                await handler(state)
        except TimeoutError as timeout:
            # Where the stage was stuck: the innermost frames of the cancelled await (file,
            # line, function only -- never locals). A stall then names its own cause.
            logger.error(
                "stage timed out",
                extra={
                    "context": {
                        "run_id": state.id,
                        "step": step,
                        "awaiting": _awaiting(timeout),
                    }
                },
            )
            raise RunFailedError(FailureCode.STAGE_TIMEOUT) from None
        state.completed_steps.append(step)
        await self._persist(state, step)
        if events.stage and events.completed and step != "publish_events":
            artifact_id = state.artifact_id if step == "persist_artifact" else None
            await self._emit(
                state, events.stage, "completed", events.completed, artifact_id=artifact_id
            )
        if self._after_step is not None:
            await self._after_step(step, state)

    async def _load_context(self, state: AnalyticsRunState) -> None:
        await self._require_chat_user(state)
        tenant = uuid.UUID(state.tenant_id)
        try:
            sources = await self._metadata.active_data_sources(tenant)
        except DependencyUnavailableError:
            raise RunFailedError(FailureCode.UPSTREAM_UNAVAILABLE) from None
        active = {str(source.id) for source in sources}
        if state.data_source_id is not None:
            if state.data_source_id not in active:
                raise RunFailedError(FailureCode.DATA_SOURCE_NOT_ACTIVE)
        elif not sources:
            raise RunFailedError(FailureCode.NO_DATA_SOURCE)
        elif len(sources) == 1:
            state.data_source_id = str(sources[0].id)
        # Several active sources and none named: left unset on purpose. Which one the question is
        # about is decided in `_retrieve_schema`, after `_classify_intent`, so the model's own
        # reading of the request (metrics, dimensions) informs the choice, not just the raw text.

    async def _classify_intent(self, state: AnalyticsRunState) -> None:
        request = await self._generate(
            state,
            stage="intent",
            system=prompts.INTENT,
            payload={"user_request": state.message, "today": utcnow().date().isoformat()},
            output_type=AnalyticsRequest,
        )
        if request.intent == "unsupported":
            raise RunFailedError(FailureCode.REQUEST_NOT_SUPPORTED)
        state.request = request

    async def _retrieve_schema(self, state: AnalyticsRunState) -> None:
        tenant = uuid.UUID(state.tenant_id)
        terms = request_terms(state.request, state.message)
        if state.data_source_id is None:
            snapshot = await self._route_data_source(state, tenant, terms)
        else:
            try:
                snapshot = await self._metadata.context(tenant, uuid.UUID(state.data_source_id))
            except DataSourceUnknownError:
                raise RunFailedError(FailureCode.DATA_SOURCE_NOT_ACTIVE) from None
            except DependencyUnavailableError:
                raise RunFailedError(FailureCode.UPSTREAM_UNAVAILABLE) from None
            if snapshot.status != "active":
                raise RunFailedError(FailureCode.DATA_SOURCE_NOT_ACTIVE)
        assert state.data_source_id is not None
        tables = rank_tables(snapshot.tables, terms, self._limits.context_max_tables)
        if not tables:
            raise RunFailedError(FailureCode.NO_RELEVANT_DATA)
        state.schema_context = SchemaContext(
            data_source_id=state.data_source_id, tables=tables, engine=snapshot.engine
        )

    async def _route_data_source(
        self, state: AnalyticsRunState, tenant: uuid.UUID, terms: list[str]
    ) -> ContextSnapshot:
        """Pick which of the tenant's active data sources the question is about.

        Authorization is unchanged, not widened: the candidates are exactly the sources an explicit
        `data_source_id` would have been accepted for (`_load_context` checks against the same
        `active_data_sources` list), and whatever is chosen still goes through the same schema
        retrieval, query-gateway validation and `authorize_query` as a named source. Only *who
        picks* changes.
        """
        try:
            sources = await self._metadata.active_data_sources(tenant)
        except DependencyUnavailableError:
            raise RunFailedError(FailureCode.UPSTREAM_UNAVAILABLE) from None
        if len(sources) > MAX_ROUTED_SOURCES:
            # Each candidate is a metadata call; past this the user is better off naming one.
            raise RunFailedError(FailureCode.DATA_SOURCE_SELECTION_REQUIRED)

        async def fetch(source_id: uuid.UUID) -> ContextSnapshot | None:
            try:
                snapshot = await self._metadata.context(tenant, source_id)
            except DataSourceUnknownError:
                return None  # deactivated since the list was read: not a candidate, not an error
            return snapshot if snapshot.status == "active" else None

        try:
            fetched = await asyncio.gather(*(fetch(source.id) for source in sources))
        except DependencyUnavailableError:
            raise RunFailedError(FailureCode.UPSTREAM_UNAVAILABLE) from None
        candidates = {
            str(source.id): snapshot
            for source, snapshot in zip(sources, fetched, strict=True)
            if snapshot is not None
        }
        if not candidates:
            raise RunFailedError(FailureCode.NO_DATA_SOURCE)
        choice = choose_data_source(
            [(source_id, snapshot.tables) for source_id, snapshot in candidates.items()], terms
        )
        if choice.source_id is None:
            raise RunFailedError(
                FailureCode.NO_RELEVANT_DATA
                if choice.reason == "no_match"
                else FailureCode.DATA_SOURCE_SELECTION_REQUIRED
            )
        state.data_source_id = choice.source_id
        return candidates[choice.source_id]

    async def _resolve_semantics(self, state: AnalyticsRunState) -> None:
        """Section 12: map business terms to *approved* definitions. No approved definition in
        the permitted context -> nothing to resolve, no model call. semantic-service unavailable
        fails the run: silently guessing a defined metric is what this step exists to prevent."""
        assert state.schema_context is not None and state.data_source_id is not None
        tenant = uuid.UUID(state.tenant_id)
        try:
            snapshot = await self._semantics.context(tenant)
        except DependencyUnavailableError:
            raise RunFailedError(FailureCode.UPSTREAM_UNAVAILABLE) from None
        state.semantic = SemanticState()
        if not snapshot.metrics and not snapshot.dimensions:
            return
        try:
            permitted = await self._metadata.context(tenant, uuid.UUID(state.data_source_id))
        except DataSourceUnknownError:
            raise RunFailedError(FailureCode.DATA_SOURCE_NOT_ACTIVE) from None
        except DependencyUnavailableError:
            raise RunFailedError(FailureCode.UPSTREAM_UNAVAILABLE) from None
        candidates = semantic_candidates(snapshot.metrics, snapshot.dimensions, permitted.tables)
        state.semantic.candidate_count = len(candidates.metrics) + len(candidates.dimensions)
        if not state.semantic.candidate_count:
            return
        assert state.request is not None
        resolution = await self._generate(
            state,
            stage="semantic",
            system=prompts.SEMANTIC,
            payload={
                "user_request": state.message,
                "request": state.request.model_dump(mode="json"),
                "metrics": [
                    m.model_dump(mode="json", include={"id", "name", "description", "synonyms"})
                    for m in candidates.metrics
                ],
                "dimensions": [
                    d.model_dump(mode="json", include={"id", "name", "synonyms"})
                    for d in candidates.dimensions
                ],
            },
            output_type=SemanticResolution,
            check=lambda resolved: resolution_problems(resolved, candidates),
        )
        metrics = [m for m in candidates.metrics if m.id in resolution.metric_ids]
        dimensions = [d for d in candidates.dimensions if d.id in resolution.dimension_ids]
        state.semantic = SemanticState(
            metrics=metrics,
            dimensions=dimensions,
            unmatched_terms=list(resolution.unmatched_terms),
            candidate_count=state.semantic.candidate_count,
        )
        # A resolved definition's table must be plannable even if lexical ranking dropped it.
        present = {t.qualified_name for t in state.schema_context.tables}
        needed = {m.column.rsplit(".", 1)[0] for m in metrics} | {
            d.column.rsplit(".", 1)[0] for d in dimensions
        }
        state.schema_context.tables.extend(
            candidates.tables[name] for name in sorted(needed - present)
        )
        state.grounding.metric_ids = [m.id for m in metrics]
        state.grounding.metric_names = [m.name for m in metrics]
        state.grounding.dimension_ids = [d.id for d in dimensions]
        state.grounding.unmatched_terms = list(resolution.unmatched_terms)

    def _semantic_payload(self, state: AnalyticsRunState) -> dict[str, object]:
        semantic = state.semantic or SemanticState()
        return {
            "metrics": [
                {"name": m.name, "measure": metric_measure(m).model_dump(mode="json")}
                for m in semantic.metrics
            ],
            "dimensions": [{"name": d.name, "column": d.column} for d in semantic.dimensions],
        }

    @staticmethod
    def _dialect(state: AnalyticsRunState) -> str:
        return state.schema_context.engine if state.schema_context else "postgres"

    def _catalog(self, state: AnalyticsRunState) -> list[dict[str, object]]:
        assert state.schema_context is not None
        return [table.model_dump(mode="json") for table in state.schema_context.tables]

    async def _build_query_plan(self, state: AnalyticsRunState) -> None:
        assert state.schema_context is not None and state.request is not None
        tables = state.schema_context.tables
        semantic = state.semantic or SemanticState()
        payload: dict[str, object] = {
            "request": state.request.model_dump(mode="json"),
            "catalog": self._catalog(state),
        }
        if semantic.metrics or semantic.dimensions:
            payload["semantic"] = self._semantic_payload(state)
        state.plan = await self._generate(
            state,
            stage="sql",
            system=prompts.PLAN,
            payload=payload,
            output_type=QueryPlan,
            check=lambda plan: (
                plan_problems(plan, tables)
                + semantic_plan_problems(plan, semantic.metrics, semantic.dimensions)
            ),
        )
        defined = {
            (metric_measure(m).column, metric_measure(m).aggregation) for m in semantic.metrics
        }
        state.grounding.measures_total = len(state.plan.measures)
        state.grounding.measures_from_metrics = sum(
            (m.column, m.aggregation) in defined for m in state.plan.measures
        )

    async def _generate_sql(
        self, state: AnalyticsRunState, problems: list[str] | None = None
    ) -> None:
        assert state.plan is not None
        payload: dict[str, object] = {
            "dialect": self._dialect(state),
            "plan": state.plan.model_dump(mode="json"),
            "catalog": self._catalog(state),
        }
        if state.semantic and state.semantic.metrics:
            payload["semantic"] = self._semantic_payload(state)
        if problems:
            payload["previous_sql"] = state.generated_sql
            payload["previous_problems"] = problems
        generated = await self._generate(
            state, stage="sql", system=prompts.SQL, payload=payload, output_type=GeneratedSql
        )
        state.generated_sql = generated.sql

    def _query_call(self, state: AnalyticsRunState, sql: str) -> QueryCall:
        assert state.data_source_id is not None
        return QueryCall(
            tenant_id=uuid.UUID(state.tenant_id),
            user_id=uuid.UUID(state.requested_by),
            run_id=uuid.UUID(state.id),
            data_source_id=uuid.UUID(state.data_source_id),
            sql=sql,
        )

    async def _validate_sql(self, state: AnalyticsRunState) -> None:
        """Section 13's validator in query-gateway; rejections feed at most 2 SQL repairs."""
        for attempt in range(self._limits.max_repairs + 1):
            assert state.generated_sql is not None
            try:
                validated = await self._queries.validate(
                    self._query_call(state, state.generated_sql)
                )
            except QueryRejectedError as rejected:
                if attempt == self._limits.max_repairs:
                    raise RunFailedError(FailureCode.QUERY_REJECTED) from None
                feedback = [
                    f"{rejected.reason}: {rejected.detail}" if rejected.detail else rejected.reason
                ]
                await self._generate_sql(state, problems=feedback)
                await self._persist(state, "validate_sql")
                continue
            except QueryDeniedError:
                raise RunFailedError(FailureCode.NOT_AUTHORIZED) from None
            except QueryNotActiveError:
                raise RunFailedError(FailureCode.DATA_SOURCE_NOT_ACTIVE) from None
            except DependencyUnavailableError:
                raise RunFailedError(FailureCode.UPSTREAM_UNAVAILABLE) from None
            # Section 8.3: a resolved metric must be computed exactly as defined -- checked on
            # the SQL query-gateway will actually run, and repaired within the same budget.
            drift = sql_metric_problems(
                validated.sql,
                state.semantic.metrics if state.semantic else [],
                dialect=self._dialect(state),
            )
            if drift:
                if attempt == self._limits.max_repairs:
                    raise RunFailedError(FailureCode.QUERY_REJECTED)
                await self._generate_sql(state, problems=drift)
                await self._persist(state, "validate_sql")
                continue
            state.validated_sql = validated.sql
            state.validated_tables = validated.tables
            return

    async def _authorize_query(self, state: AnalyticsRunState) -> None:
        """Deterministic: the user may still chat, and the query stays inside the agent context."""
        await self._require_chat_user(state)
        assert state.schema_context is not None
        allowed = {table.qualified_name for table in state.schema_context.tables}
        if not state.validated_tables or not set(state.validated_tables) <= allowed:
            raise RunFailedError(FailureCode.NOT_AUTHORIZED)

    async def _execute_query(self, state: AnalyticsRunState) -> None:
        assert state.generated_sql is not None
        try:
            state.execution = await self._execute_with_capacity_retries(state, state.generated_sql)
        except QueryCapacityError:
            raise RunFailedError(FailureCode.QUERY_CONCURRENCY_LIMITED) from None
        except QueryRejectedError:
            raise RunFailedError(FailureCode.QUERY_REJECTED) from None
        except QueryDeniedError:
            raise RunFailedError(FailureCode.NOT_AUTHORIZED) from None
        except QueryNotActiveError:
            raise RunFailedError(FailureCode.DATA_SOURCE_NOT_ACTIVE) from None
        except QueryTimedOutError:
            raise RunFailedError(FailureCode.QUERY_TIMEOUT) from None
        except QueryExecutionError:
            raise RunFailedError(FailureCode.QUERY_FAILED) from None
        except DependencyUnavailableError:
            raise RunFailedError(FailureCode.UPSTREAM_UNAVAILABLE) from None

    async def _execute_with_capacity_retries(
        self, state: AnalyticsRunState, sql: str
    ) -> ExecutionSummary:
        """The tenant's other queries finish in seconds: wait a little (exponential backoff)
        rather than fail a run the user is watching."""
        for attempt in range(self._limits.query_capacity_retries + 1):
            try:
                return await self._queries.execute(
                    self._query_call(state, sql),
                    max_rows=self._limits.query_max_rows,
                    timeout_ms=self._limits.query_timeout_ms,
                )
            except QueryCapacityError:
                if attempt == self._limits.query_capacity_retries:
                    raise
                await asyncio.sleep(self._limits.query_capacity_backoff_seconds * 2**attempt)
        raise QueryCapacityError()  # unreachable: the loop returns or raises

    async def _analyze_result(self, state: AnalyticsRunState) -> None:
        """ADR 0009: the model reads aggregate statistics only; every number it writes must be
        grounded. An ungrounded or failed insight falls back to the deterministic summary --
        only budget failures stop the run."""
        assert state.execution is not None and state.request is not None
        stats = state.execution.stats
        if stats is None:
            state.grounding.insight_fallback = True
            return
        request_text = f"{state.message} {state.request.title}"
        try:
            state.insight = await self._generate(
                state,
                stage="visualization",
                system=prompts.INSIGHT,
                payload={
                    "request": {"title": state.request.title, "user_request": state.message},
                    "result_stats": stats.model_dump(mode="json"),
                },
                output_type=ResultInsight,
                check=lambda insight: insight_problems(insight, stats, request_text),
            )
        except RunFailedError as failure:
            if failure.code in (
                FailureCode.RUN_BUDGET_EXCEEDED,
                FailureCode.TENANT_BUDGET_EXCEEDED,
                FailureCode.BUDGET_UNAVAILABLE,
            ):
                raise
            state.insight = None
            state.grounding.insight_grounded = False
            state.grounding.insight_fallback = True
            return
        state.grounding.insight_grounded = True

    async def _chart_problems(self, state: AnalyticsRunState, spec: ChartSpec) -> list[str]:
        """visualization-service is the one validator (Section 17); an outage fails the run."""
        assert state.execution is not None
        try:
            check = await self._charts.check(
                spec.to_wire(),
                [field.model_dump(mode="json") for field in state.execution.result_schema],
            )
        except DependencyUnavailableError:
            raise RunFailedError(FailureCode.UPSTREAM_UNAVAILABLE) from None
        return [] if check.valid else (check.problems or ["chart specification rejected"])

    async def _build_chart_spec(self, state: AnalyticsRunState) -> None:
        assert state.execution is not None and state.request is not None
        state.chart_spec = await self._generate(
            state,
            stage="visualization",
            system=prompts.CHART,
            payload={
                "request": {
                    "title": state.request.title,
                    "chart_preference": state.request.chart_preference,
                },
                "result_schema": [
                    field.model_dump(mode="json") for field in state.execution.result_schema
                ],
            },
            output_type=ChartSpec,
            check=lambda spec: self._chart_problems(state, spec),
            exhausted=FailureCode.CHART_INVALID,
        )

    async def _validate_chart_spec(self, state: AnalyticsRunState) -> None:
        """Section 17, deterministic: the stored spec is re-validated, never trusted from memory."""
        if state.chart_spec is None or await self._chart_problems(state, state.chart_spec):
            raise RunFailedError(FailureCode.CHART_INVALID)

    async def _persist_artifact(self, state: AnalyticsRunState) -> None:
        """Store the artifact in dashboard-service (Section 8.9) under an id derived from the run,
        then the assistant message -- both idempotent, so a resumed step never duplicates them."""
        assert (
            state.execution
            and state.chart_spec
            and state.request
            and state.plan
            and state.validated_sql
            and state.data_source_id
        )
        rows = state.execution.row_count
        summary = (
            state.insight.headline
            if state.insight is not None
            else f"{state.request.title} — {rows} row{'s' if rows != 1 else ''}"
            + (" (truncated)" if state.execution.truncated else "")
        )
        artifact_id = uuid.uuid5(ARTIFACT_NAMESPACE, state.id)
        semantic = state.semantic or SemanticState()
        try:
            await self._artifacts.store(
                ArtifactDraft(
                    artifact_id=artifact_id,
                    tenant_id=uuid.UUID(state.tenant_id),
                    conversation_id=uuid.UUID(state.conversation_id),
                    run_id=uuid.UUID(state.id),
                    title=state.request.title,
                    summary=summary,
                    semantic_query={
                        **state.plan.model_dump(mode="json"),
                        "semantic": {
                            "metrics": [{"id": m.id, "name": m.name} for m in semantic.metrics],
                            "dimensions": [
                                {"id": d.id, "name": d.name} for d in semantic.dimensions
                            ],
                        },
                    },
                    source_refs=[
                        {"data_source_id": state.data_source_id, "tables": state.validated_tables}
                    ],
                    validated_sql=state.validated_sql,
                    query_result_ref=state.execution.result_handle,
                    result_schema=[
                        field.model_dump(mode="json") for field in state.execution.result_schema
                    ],
                    chart_spec=state.chart_spec.to_wire(),
                    created_by=uuid.UUID(state.requested_by),
                )
            )
        except ArtifactRejectedError:
            raise RunFailedError(FailureCode.CHART_INVALID) from None
        except DependencyUnavailableError:
            raise RunFailedError(FailureCode.UPSTREAM_UNAVAILABLE) from None
        state.artifact_id = str(artifact_id)
        tenant = uuid.UUID(state.tenant_id)
        async with tenant_scope(self._sessions, tenant) as db:
            repository = AnalyticsRepository(db)
            if not await repository.has_assistant_message(tenant, uuid.UUID(state.id)):
                await repository.add_message(
                    Message(
                        tenant_id=tenant,
                        conversation_id=uuid.UUID(state.conversation_id),
                        role="assistant",
                        content=summary,
                        run_id=uuid.UUID(state.id),
                    )
                )
            await db.commit()

    async def _publish_events(self, state: AnalyticsRunState) -> None:
        tenant = uuid.UUID(state.tenant_id)
        async with tenant_scope(self._sessions, tenant) as db:
            repository = AnalyticsRepository(db)
            run = await repository.get_run(tenant, uuid.UUID(state.id), for_update=True)
            if run is None:
                raise NotFoundError()
            if run.status == "cancelled":
                raise RunFailedError(FailureCode.CANCELLED)
            event = await repository.append_event(
                run,
                stage="run",
                status="completed",
                message=STEP_EVENTS["publish_events"].completed or "",
                artifact_id=None,
            )
            state.emitted_events.append("run.completed")
            await repository.finish_run(run, "completed", None, state.model_dump(mode="json"))
            await db.commit()
        await self._publish(state, event)

    # --- helpers ----------------------------------------------------------------------------

    async def _require_chat_user(self, state: AnalyticsRunState) -> None:
        try:
            principal = await self._identity.resolve(
                uuid.UUID(state.tenant_id), uuid.UUID(state.requested_by)
            )
        except DelegatedUserDeniedError:
            raise RunFailedError(FailureCode.NOT_AUTHORIZED) from None
        except DependencyUnavailableError:
            raise RunFailedError(FailureCode.UPSTREAM_UNAVAILABLE) from None
        if not principal.has_permission(PERM_CHAT_USE):
            raise RunFailedError(FailureCode.NOT_AUTHORIZED)

    async def _ensure_not_cancelled(self, state: AnalyticsRunState) -> None:
        tenant = uuid.UUID(state.tenant_id)
        async with tenant_scope(self._sessions, tenant) as db:
            run = await AnalyticsRepository(db).get_run(tenant, uuid.UUID(state.id))
            await db.commit()
        if run is None or run.status == "cancelled":
            raise RunFailedError(FailureCode.CANCELLED)

    async def _generate(
        self,
        state: AnalyticsRunState,
        *,
        stage: str,
        system: str,
        payload: Mapping[str, Any],
        output_type: type[OutputT],
        check: Callable[[OutputT], list[str] | Awaitable[list[str]]] | None = None,
        exhausted: FailureCode = FailureCode.OUTPUT_INVALID,
    ) -> OutputT:
        return await self._router.generate(
            state,
            stage=stage,
            system=system,
            payload=payload,
            output_type=output_type,
            check=check,
            exhausted=exhausted,
            on_charged=self._persist_usage,
        )

    async def _persist_usage(self, state: AnalyticsRunState) -> None:
        """Record run token usage as soon as a call is charged, without persisting the unfinished
        step's partial outputs: a resumed run keeps paying from where it was, never from zero."""
        tenant = uuid.UUID(state.tenant_id)
        async with tenant_scope(self._sessions, tenant) as db:
            repository = AnalyticsRepository(db)
            run = await repository.get_run(tenant, uuid.UUID(state.id), for_update=True)
            if run is None:
                raise NotFoundError()
            saved = dict(run.flow_state)
            saved["usage"] = state.usage.model_dump(mode="json")
            await repository.save_flow_state(run, saved, current_stage=run.current_stage)
            await db.commit()

    async def _persist(self, state: AnalyticsRunState, step: str) -> None:
        tenant = uuid.UUID(state.tenant_id)
        async with tenant_scope(self._sessions, tenant) as db:
            repository = AnalyticsRepository(db)
            run = await repository.get_run(tenant, uuid.UUID(state.id), for_update=True)
            if run is None:
                raise NotFoundError()
            await repository.save_flow_state(run, state.model_dump(mode="json"), current_stage=step)
            await db.commit()

    async def _emit(
        self,
        state: AnalyticsRunState,
        stage: str,
        status: str,
        message: str,
        artifact_id: str | None = None,
    ) -> None:
        key = f"{stage}.{status}"
        if key in state.emitted_events:
            return
        tenant = uuid.UUID(state.tenant_id)
        async with tenant_scope(self._sessions, tenant) as db:
            repository = AnalyticsRepository(db)
            run = await repository.get_run(tenant, uuid.UUID(state.id), for_update=True)
            if run is None:
                raise NotFoundError()
            event = await repository.append_event(
                run,
                stage=stage,
                status=status,
                message=message,
                artifact_id=uuid.UUID(artifact_id) if artifact_id else None,
            )
            state.emitted_events.append(key)
            await repository.save_flow_state(
                run, state.model_dump(mode="json"), current_stage=run.current_stage
            )
            await db.commit()
        await self._publish(state, event)

    async def _publish(self, state: AnalyticsRunState, event: object) -> None:
        wire = AnalyticsRunEvent(
            run_id=state.id,
            seq=event.seq,  # type: ignore[attr-defined]
            stage=event.stage,  # type: ignore[attr-defined]
            status=event.status,  # type: ignore[attr-defined]
            message=event.message,  # type: ignore[attr-defined]
            artifact_id=str(event.artifact_id) if event.artifact_id else None,  # type: ignore[attr-defined]
            created_at=event.created_at,  # type: ignore[attr-defined]
        )
        try:
            await self._events.publish(wire)
        except Exception:
            # Postgres is the record; SSE replays from it (Section 18).
            logger.warning(
                "run event publish failed", extra={"context": {"run_id": state.id, "seq": wire.seq}}
            )

    async def _fail(self, state: AnalyticsRunState, code: FailureCode) -> None:
        """End the run with `code`. Progress and emitted events come from the *persisted* flow state
        (a step or event counts only once persisted); token usage comes from the live state, since
        a failing step may already have been charged for model calls it never persisted."""
        tenant = uuid.UUID(state.tenant_id)
        text = MESSAGES[code]
        published: list[object] = []
        async with tenant_scope(self._sessions, tenant) as db:
            repository = AnalyticsRepository(db)
            run = await repository.get_run(tenant, uuid.UUID(state.id), for_update=True)
            if run is None:
                return
            current = AnalyticsRunState.model_validate(run.flow_state)
            if state.usage.total >= current.usage.total:
                current.usage = state.usage
            failed_stage = next(
                (
                    STEP_EVENTS[step].stage
                    for step in FLOW_STEPS
                    if step not in current.completed_steps and STEP_EVENTS[step].stage
                ),
                None,
            )
            final_status = (
                "cancelled"
                if run.status == "cancelled" or code is FailureCode.CANCELLED
                else "failed"
            )
            emitted = current.emitted_events
            if (
                failed_stage
                and failed_stage != "run"
                and f"{failed_stage}.started" in emitted
                and f"{failed_stage}.{final_status}" not in emitted
            ):
                published.append(
                    await repository.append_event(
                        run,
                        stage=failed_stage,
                        status=final_status,
                        message=text,
                        artifact_id=None,
                    )
                )
                emitted.append(f"{failed_stage}.{final_status}")
            if (
                "run.failed" not in emitted
                and "run.completed" not in emitted
                and "run.cancelled" not in emitted
            ):
                published.append(
                    await repository.append_event(
                        run, stage="run", status=final_status, message=text, artifact_id=None
                    )
                )
                emitted.append(f"run.{final_status}")
            await repository.finish_run(
                run, final_status, code.value, current.model_dump(mode="json")
            )
            await repository.add_message(
                Message(
                    tenant_id=tenant,
                    conversation_id=uuid.UUID(current.conversation_id),
                    role="assistant",
                    content=text,
                    run_id=uuid.UUID(current.id),
                )
            )
            await db.commit()
        for event in published:
            await self._publish(current, event)


def _awaiting(error: BaseException, depth: int = 8) -> list[str]:
    """The innermost `depth` frames of the cancellation behind a timeout."""
    cancelled = error.__context__ or error
    frames = traceback.extract_tb(cancelled.__traceback__)[-depth:]
    return [f"{Path(f.filename).name}:{f.lineno}:{f.name}" for f in frames]
