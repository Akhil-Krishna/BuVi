"""Contract: committed `contracts/openapi/visualization-service.json` matches the code.
`BUVI_UPDATE_CONTRACTS=1` rewrites it."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from visualization_service.main import create_app

pytestmark = pytest.mark.contract

CONTRACT = (
    Path(__file__).resolve().parents[6] / "contracts" / "openapi" / "visualization-service.json"
)


def test_export_matches_committed_contract() -> None:
    generated = create_app().openapi()
    if os.environ.get("BUVI_UPDATE_CONTRACTS") == "1":
        CONTRACT.write_text(json.dumps(generated, indent=2, sort_keys=True) + "\n")
    assert CONTRACT.is_file(), (
        "contracts/openapi/visualization-service.json missing: run `make contracts`"
    )
    assert json.loads(CONTRACT.read_text()) == generated, "contract drift: run `make contracts`"
