"""scripts/replay_usage_events.py finds exactly the logged undelivered usage events, once each."""

from __future__ import annotations

import importlib.util
import json
import uuid
from pathlib import Path
from types import ModuleType

from platform_contracts import BillingUsageRecorded

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "replay_usage_events.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("replay_usage_events", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_only_undelivered_events_are_replayed_and_duplicates_collapse() -> None:
    event = BillingUsageRecorded(tenant_id=uuid.uuid4(), metric="query_execution_ms", quantity=5)
    line = json.dumps(
        {
            "level": "ERROR",
            "message": "usage event undelivered",
            "context": {"event": event.model_dump(mode="json"), "error_type": "ConnectionError"},
        }
    )
    other = json.dumps({"level": "WARNING", "message": "something else", "context": {}})
    found = _load().undelivered([line, other, "not json", line])
    assert found == [event]
