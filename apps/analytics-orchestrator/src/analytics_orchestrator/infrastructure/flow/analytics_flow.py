"""The CrewAI `AnalyticsFlow` (Section 10): the durable step sequence, and nothing more.

Each Flow method is a CrewAI `@start`/`@listen` step that hands its state to
`RunExecutor.run_step`, where skipping completed steps, timeouts, persistence and events live.
The framework owns ordering; the application owns the rules (Section 30.1: "Do not let an LLM
framework become the application architecture"). LLM stages go through the ModelRouter, not
CrewAI agents (ADR 0006).

CrewAI's `FlowPersistence` is synchronous; `flow_state` is persisted asynchronously by the
executor after every step instead, which is the Section 10.2 guarantee.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from crewai.flow.flow import Flow, listen, start
from pydantic import PrivateAttr

from analytics_orchestrator.domain.value_objects.run_state import AnalyticsRunState

if TYPE_CHECKING:
    from analytics_orchestrator.application.services.run_executor import RunExecutor


def silence_crewai_console() -> None:
    """CrewAI prints rich progress panels to stdout; service logs are JSON (Section 22)."""
    try:
        from crewai.events.event_listener import event_listener

        event_listener.formatter.verbose = False
        event_listener.formatter.console.quiet = True
    except Exception:  # noqa: S110 - cosmetic; never block startup on the framework's console
        pass


class AnalyticsFlowState(AnalyticsRunState):
    """CrewAI builds the Flow's state with no arguments before `kickoff` supplies inputs, so the
    Flow-side type defaults its identity fields. The executor only ever hands the Flow a state
    loaded from `analytics.runs.flow_state`, which is validated as `AnalyticsRunState` first."""

    id: str = ""
    tenant_id: str = ""
    conversation_id: str = ""
    requested_by: str = ""
    message: str = ""


class AnalyticsFlow(Flow[AnalyticsFlowState]):
    #: CrewAI's Flow is a pydantic model: non-field attributes must be private attributes.
    _executor: Any = PrivateAttr(default=None)

    def __init__(self, executor: RunExecutor, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._executor = executor

    async def _step(self, name: str) -> None:
        await self._executor.run_step(self.state, name)

    @start()
    async def load_context(self) -> None:
        await self._step("load_context")

    @listen(load_context)
    async def classify_intent(self) -> None:
        await self._step("classify_intent")

    @listen(classify_intent)
    async def retrieve_schema(self) -> None:
        await self._step("retrieve_schema")

    @listen(retrieve_schema)
    async def build_query_plan(self) -> None:
        await self._step("build_query_plan")

    @listen(build_query_plan)
    async def generate_sql(self) -> None:
        await self._step("generate_sql")

    @listen(generate_sql)
    async def validate_sql(self) -> None:
        await self._step("validate_sql")

    @listen(validate_sql)
    async def authorize_query(self) -> None:
        await self._step("authorize_query")

    @listen(authorize_query)
    async def execute_query(self) -> None:
        await self._step("execute_query")

    @listen(execute_query)
    async def build_chart_spec(self) -> None:
        await self._step("build_chart_spec")

    @listen(build_chart_spec)
    async def validate_chart_spec(self) -> None:
        await self._step("validate_chart_spec")

    @listen(validate_chart_spec)
    async def persist_artifact(self) -> None:
        await self._step("persist_artifact")

    @listen(persist_artifact)
    async def publish_events(self) -> None:
        await self._step("publish_events")


class CrewAiFlowRunner:
    async def run(self, state: AnalyticsRunState, executor: RunExecutor) -> AnalyticsRunState:
        flow = AnalyticsFlow(executor)
        await flow.kickoff_async(inputs=state.model_dump(mode="json"))
        return flow.state
