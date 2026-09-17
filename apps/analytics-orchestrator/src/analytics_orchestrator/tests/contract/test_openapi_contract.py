"""Contract: committed `contracts/openapi/analytics-orchestrator.json` matches the code, and no
response schema exposes prompts, SQL, rows or reasoning (Section 11). `BUVI_UPDATE_CONTRACTS=1`."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from analytics_orchestrator.main import create_app

pytestmark = pytest.mark.contract

CONTRACT = (
    Path(__file__).resolve().parents[6] / "contracts" / "openapi" / "analytics-orchestrator.json"
)
FORBIDDEN = {"prompt", "sql", "rows", "reasoning", "thoughts", "flow_state", "secret_ref"}


def _generated() -> dict[str, Any]:
    return create_app().openapi()


def test_export_matches_committed_contract() -> None:
    generated = _generated()
    if os.environ.get("BUVI_UPDATE_CONTRACTS") == "1":
        CONTRACT.write_text(json.dumps(generated, indent=2, sort_keys=True) + "\n")
    assert CONTRACT.is_file(), (
        "contracts/openapi/analytics-orchestrator.json missing: run `make contracts`"
    )
    assert json.loads(CONTRACT.read_text()) == generated, "contract drift: run `make contracts`"


def test_responses_expose_no_model_internals() -> None:
    schemas = _generated()["components"]["schemas"]
    for name, schema in schemas.items():
        if name.endswith("Response"):
            assert not set(schema.get("properties", {})) & FORBIDDEN, name
