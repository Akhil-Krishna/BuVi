"""Contract: committed `contracts/openapi/semantic-service.json` matches the code.
`BUVI_UPDATE_CONTRACTS=1` rewrites it."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from semantic_service.main import create_app

pytestmark = pytest.mark.contract

CONTRACT = Path(__file__).resolve().parents[6] / "contracts" / "openapi" / "semantic-service.json"


def test_export_matches_committed_contract() -> None:
    generated = create_app().openapi()
    if os.environ.get("BUVI_UPDATE_CONTRACTS") == "1":
        CONTRACT.write_text(json.dumps(generated, indent=2, sort_keys=True) + "\n")
    assert CONTRACT.is_file(), (
        "contracts/openapi/semantic-service.json missing: run `make contracts`"
    )
    assert json.loads(CONTRACT.read_text()) == generated, "contract drift: run `make contracts`"
