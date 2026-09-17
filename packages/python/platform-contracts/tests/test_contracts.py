"""ChartSpec DTO strictness (Section 17), run-event wire shape (Section 11), event versioning (18.1),
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
    DashboardTilePinned,
    RunRequested,
    SchemaVersionError,
)

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[4]
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


def test_one_spelling_per_key_and_no_coercion() -> None:
    with pytest.raises(ValidationError):
        ChartSpec.model_validate({**LINE, "options": {"color_scheme": "default"}})
    with pytest.raises(ValidationError):
        ChartSpec.model_validate({**LINE, "options": {"legend": "true"}})
    spec = ChartSpec.model_validate({**LINE, "options": {"colorScheme": "categorical"}})
    assert spec.options.color_scheme == "categorical"
    assert "colorScheme" in json.dumps(spec.to_wire())


def test_tile_pinned_event_is_versioned() -> None:
    body = {
        k: str(uuid.uuid4())
        for k in ("tenant_id", "dashboard_id", "tile_id", "artifact_id", "user_id")
    }
    assert DashboardTilePinned.parse_event(body).schema_version == "1.0"
    with pytest.raises(SchemaVersionError):
        DashboardTilePinned.parse_event({**body, "schema_version": "2.0"})


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


def test_spec_round_trips_through_a_default_dump() -> None:
    """Persisted run state uses `model_dump()`; it must validate back unchanged."""
    spec = ChartSpec.model_validate({**LINE, "options": {"colorScheme": "categorical"}})
    assert ChartSpec.model_validate(spec.model_dump(mode="json")) == spec
