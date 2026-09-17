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
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analytics_orchestrator.application.services import prompts
from analytics_orchestrator.application.services.model_router import ModelRouter
from analytics_orchestrator.application.services.ports import (
    DataSourceUnknownError,
    DelegatedIdentity,
    DelegatedUserDeniedError,
    DependencyUnavailableError,
    MetadataContext,
    OutputT,
    QueryCall,
    QueryDeniedError,
    QueryExecutionError,
    QueryGateway,
    QueryNotActiveError,
    QueryRejectedError,
    QueryTimedOutError,
    RunEventPublisher,
    utcnow,
)
from analytics_orchestrator.domain.errors import NotFoundError, RunBusyError
from analytics_orchestrator.domain.policies.context_policy import (
    plan_problems,
    rank_tables,
    request_terms,
)
from analytics_orchestrator.domain.policies.flow_steps import (
    FLOW_STEPS,
    STEP_EVENTS,
    TERMINAL_STATUSES,
)
from analytics_orchestrator.domain.value_objects.agent_outputs import (
    AnalyticsRequest,
    GeneratedSql,
    QueryPlan,
)
from analytics_orchestrator.domain.value_objects.failures import (
    MESSAGES,
    FailureCode,
    RunFailedError,
)
from analytics_orchestrator.domain.value_objects.run_state import (
    AnalyticsRunState,
    ArtifactRecord,
    SchemaContext,
    SourceRef,
)
from analytics_orchestrator.infrastructure.db.models import Message
from analytics_orchestrator.infrastructure.db.repositories.analytics_repository import (
    AnalyticsRepository,
)
from analytics_orchestrator.infrastructure.db.session import tenant_scope
from platform_auth.permissions import PERM_CHAT_USE
from platform_contracts import AnalyticsRunEvent, ChartSpec, ChartSpecError, validate_chart_spec

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FlowLimits:
    stage_timeout_seconds: float
    run_timeout_seconds: float
    query_max_rows: int
    query_timeout_ms: int
    context_max_tables: int
    max_repairs: int


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
        except TimeoutError:
            raise RunFailedError(FailureCode.STAGE_TIMEOUT) from None
        state.completed_steps.append(step)
        await self._persist(state, step)
        if events.stage and events.completed and step != "publish_events":
            artifact_id = (
                state.artifact.artifact_id
                if step == "persist_artifact" and state.artifact
                else None
            )
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
        elif len(sources) > 1:
            raise RunFailedError(FailureCode.DATA_SOURCE_SELECTION_REQUIRED)
        else:
            state.data_source_id = str(sources[0].id)

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
        assert state.data_source_id is not None
        try:
            snapshot = await self._metadata.context(
                uuid.UUID(state.tenant_id), uuid.UUID(state.data_source_id)
            )
        except DataSourceUnknownError:
            raise RunFailedError(FailureCode.DATA_SOURCE_NOT_ACTIVE) from None
        except DependencyUnavailableError:
            raise RunFailedError(FailureCode.UPSTREAM_UNAVAILABLE) from None
        if snapshot.status != "active":
            raise RunFailedError(FailureCode.DATA_SOURCE_NOT_ACTIVE)
        tables = rank_tables(
            snapshot.tables,
            request_terms(state.request, state.message),
            self._limits.context_max_tables,
        )
        if not tables:
            raise RunFailedError(FailureCode.NO_RELEVANT_DATA)
        state.schema_context = SchemaContext(data_source_id=state.data_source_id, tables=tables)

    def _catalog(self, state: AnalyticsRunState) -> list[dict[str, object]]:
        assert state.schema_context is not None
        return [table.model_dump(mode="json") for table in state.schema_context.tables]

    async def _build_query_plan(self, state: AnalyticsRunState) -> None:
        assert state.schema_context is not None and state.request is not None
        tables = state.schema_context.tables
        state.plan = await self._generate(
            state,
            stage="sql",
            system=prompts.PLAN,
            payload={
                "request": state.request.model_dump(mode="json"),
                "catalog": self._catalog(state),
            },
            output_type=QueryPlan,
            check=lambda plan: plan_problems(plan, tables),
        )

    async def _generate_sql(
        self, state: AnalyticsRunState, problems: list[str] | None = None
    ) -> None:
        assert state.plan is not None
        payload: dict[str, object] = {
            "plan": state.plan.model_dump(mode="json"),
            "catalog": self._catalog(state),
        }
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
            state.execution = await self._queries.execute(
                self._query_call(state, state.generated_sql),
                max_rows=self._limits.query_max_rows,
                timeout_ms=self._limits.query_timeout_ms,
            )
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

    def _chart_problems(self, state: AnalyticsRunState, spec: ChartSpec) -> list[str]:
        assert state.execution is not None
        try:
            validate_chart_spec(spec, state.execution.result_schema)
        except ChartSpecError as error:
            return error.problems
        return []

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
        if state.chart_spec is None or self._chart_problems(state, state.chart_spec):
            raise RunFailedError(FailureCode.CHART_INVALID)

    async def _persist_artifact(self, state: AnalyticsRunState) -> None:
        assert (
            state.execution
            and state.chart_spec
            and state.request
            and state.validated_sql
            and state.data_source_id
        )
        rows = state.execution.row_count
        summary = f"{state.request.title} — {rows} row{'s' if rows != 1 else ''}" + (
            " (truncated)" if state.execution.truncated else ""
        )
        state.artifact = ArtifactRecord(
            artifact_id=str(uuid.uuid4()),
            tenant_id=state.tenant_id,
            conversation_id=state.conversation_id,
            run_id=state.id,
            title=state.request.title,
            summary=summary,
            validated_sql=state.validated_sql,
            query_id=state.execution.query_id,
            query_result_ref=state.execution.result_handle,
            result_schema=state.execution.result_schema,
            chart_spec=state.chart_spec.to_wire(),
            source_refs=[
                SourceRef(data_source_id=state.data_source_id, tables=state.validated_tables)
            ],
            created_by=state.requested_by,
            created_at=utcnow(),
        )
        tenant = uuid.UUID(state.tenant_id)
        async with tenant_scope(self._sessions, tenant) as db:
            await AnalyticsRepository(db).add_message(
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
        check: Callable[[OutputT], list[str]] | None = None,
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
            emitted = current.emitted_events
            if (
                failed_stage
                and failed_stage != "run"
                and f"{failed_stage}.started" in emitted
                and f"{failed_stage}.failed" not in emitted
            ):
                published.append(
                    await repository.append_event(
                        run, stage=failed_stage, status="failed", message=text, artifact_id=None
                    )
                )
                emitted.append(f"{failed_stage}.failed")
            if "run.failed" not in emitted and "run.completed" not in emitted:
                published.append(
                    await repository.append_event(
                        run, stage="run", status="failed", message=text, artifact_id=None
                    )
                )
                emitted.append("run.failed")
            final_status = (
                "cancelled"
                if run.status == "cancelled" or code is FailureCode.CANCELLED
                else "failed"
            )
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
