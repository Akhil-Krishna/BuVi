"""The AnalyticsFlow's steps and the user-safe events they emit (Sections 10, 11, 32).

Several Flow steps share one user-visible stage (e.g. `build_query_plan` and `generate_sql` are
both "sql"): the first step of a stage emits `started`, the last emits `completed`. Messages are
fixed strings -- never model output.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

FLOW_STEPS: Final[tuple[str, ...]] = (
    "load_context",
    "classify_intent",
    "retrieve_schema",
    "build_query_plan",
    "generate_sql",
    "validate_sql",
    "authorize_query",
    "execute_query",
    "build_chart_spec",
    "validate_chart_spec",
    "persist_artifact",
    "publish_events",
)


@dataclass(frozen=True)
class StepEvents:
    stage: str | None
    started: str | None = None
    completed: str | None = None


STEP_EVENTS: Final[dict[str, StepEvents]] = {
    "load_context": StepEvents(None),
    "classify_intent": StepEvents("intent", "Understanding your request", "Request understood"),
    "retrieve_schema": StepEvents("schema", "Finding relevant data", "Relevant data found"),
    "build_query_plan": StepEvents("sql", started="Building the query"),
    "generate_sql": StepEvents("sql", completed="Query drafted"),
    "validate_sql": StepEvents("validation", started="Validating the query"),
    "authorize_query": StepEvents("validation", completed="Query validated"),
    "execute_query": StepEvents("execution", "Running the query", "Query finished"),
    "build_chart_spec": StepEvents("visualization", started="Creating the visualization"),
    "validate_chart_spec": StepEvents("visualization", completed="Visualization ready"),
    "persist_artifact": StepEvents("artifact", "Saving the result", "Result saved"),
    "publish_events": StepEvents("run", completed="Your analysis is ready"),
}

TERMINAL_STATUSES: Final = frozenset({"completed", "failed", "cancelled"})


def stage_of(step: str) -> str | None:
    return STEP_EVENTS[step].stage
