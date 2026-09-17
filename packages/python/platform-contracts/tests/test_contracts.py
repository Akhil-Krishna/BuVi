"""ChartSpec strictness (Section 17), run-event wire shape (Section 11), event versioning (18.1),
and drift between these models and the committed JSON Schemas."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

from platform_contracts import (
    AnalyticsRunEvent,
    ChartSpec,
    ChartSpecError,
    ResultField,
    RunRequested,
    SchemaVersionError,
    validate_chart_spec,
)

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[4]
RESULT = [
    ResultField(field="month", type="temporal"),
    ResultField(field="revenue", type="quantitative"),
    ResultField(field="region", type="nominal"),
]
LINE = {
    "type": "line",
    "dataset": "artifact-result",
    "encoding": {
        "x": {"field": "month", "type": "temporal"},
        "y": {"field": "revenue", "type": "quantitative"},
    },
    "options": {"title": "Monthly Revenue", "legend": True},
}


def test_section_17_example_is_valid() -> None:
    spec = ChartSpec.model_validate(LINE)
    validate_chart_spec(spec, RESULT)
    assert spec.options.title == "Monthly Revenue"
    assert spec.to_wire()["dataset"] == "artifact-result"


@pytest.mark.parametrize(
    "mutation",
    [
        {"type": "sankey"},
        {"dataset": "other-artifact"},
        {"script": "alert(1)"},
        {"options": {"title": "<img src=x onerror=alert(1)>"}},
        {"options": {"title": "{{constructor}}"}},
        {"options": {"html": "<b>x</b>"}},
        {"encoding": {"x": {"field": "month", "type": "temporal", "format": "x"}}},
        {"encoding": {"x": {"field": "month; drop", "type": "temporal"}}},
    ],
)
def test_unknown_keys_and_markup_are_rejected(mutation: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ChartSpec.model_validate({**LINE, **mutation})


@pytest.mark.parametrize(
    ("encoding", "chart_type"),
    [
        (
            {
                "x": {"field": "month", "type": "temporal"},
                "y": {"field": "profit", "type": "quantitative"},
            },
            "line",
        ),
        (
            {
                "x": {"field": "month", "type": "temporal"},
                "y": {"field": "region", "type": "nominal"},
            },
            "bar",
        ),
        (
            {
                "x": {"field": "revenue", "type": "temporal"},
                "y": {"field": "revenue", "type": "quantitative"},
            },
            "line",
        ),
        ({"x": {"field": "month", "type": "temporal"}}, "line"),
        (
            {
                "x": {"field": "region", "type": "nominal"},
                "y": {"field": "revenue", "type": "quantitative"},
                "color": {"field": "revenue", "type": "quantitative"},
            },
            "bar",
        ),
    ],
)
def test_encodings_must_fit_the_result_schema(encoding: dict[str, object], chart_type: str) -> None:
    spec = ChartSpec.model_validate({"type": chart_type, "encoding": encoding})
    with pytest.raises(ChartSpecError) as info:
        validate_chart_spec(spec, RESULT)
    assert info.value.problems


def test_table_needs_no_encoding() -> None:
    validate_chart_spec(ChartSpec.model_validate({"type": "table"}), RESULT)


def test_run_event_wire_shape_matches_section_11() -> None:
    event = AnalyticsRunEvent(
        run_id="r1",
        seq=3,
        stage="artifact",
        status="completed",
        message="Result saved.",
        artifact_id="a1",
        created_at=dt.datetime(2026, 9, 17, tzinfo=dt.UTC),
    )
    assert event.to_wire() == {
        "runId": "r1",
        "seq": 3,
        "stage": "artifact",
        "status": "completed",
        "message": "Result saved.",
        "artifactId": "a1",
        "createdAt": "2026-09-17T00:00:00Z",
    }
    assert event.event_name == "artifact.completed" and not event.is_terminal
    assert AnalyticsRunEvent.model_validate(event.to_wire()) == event
    with pytest.raises(ValidationError):
        AnalyticsRunEvent.model_validate({**event.to_wire(), "stage": "thinking"})


def test_consumers_reject_unknown_major_versions() -> None:
    body = {
        "run_id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "conversation_id": str(uuid.uuid4()),
    }
    assert RunRequested.parse_event(json.dumps(body)).schema_version == "1.0"
    assert RunRequested.parse_event({**body, "schema_version": "1.4"})
    with pytest.raises(SchemaVersionError):
        RunRequested.parse_event({**body, "schema_version": "2.0"})
    with pytest.raises(ValidationError):
        RunRequested.parse_event({**body, "unexpected": True})


def test_committed_json_schemas_match_the_models() -> None:
    spec = importlib.util.spec_from_file_location(
        "export_json_schemas", REPO / "scripts" / "export_json_schemas.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    generated = module.schemas()
    if os.environ.get("BUVI_UPDATE_CONTRACTS") == "1":
        module.write(REPO)
    for relative, schema in generated.items():
        path = REPO / relative
        assert path.is_file(), f"{relative} missing: run `make contracts`"
        assert json.loads(path.read_text()) == schema, f"{relative} drift: run `make contracts`"
