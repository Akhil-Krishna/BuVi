"""Contract: committed `contracts/openapi/dashboard-service.json` matches the code, and no response
schema exposes the SQL or the result handle. `BUVI_UPDATE_CONTRACTS=1` rewrites it."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from dashboard_service.main import create_app

pytestmark = pytest.mark.contract

CONTRACT = Path(__file__).resolve().parents[6] / "contracts" / "openapi" / "dashboard-service.json"
FORBIDDEN = {"validated_sql", "query_result_ref", "result_handle", "sql", "secret_ref"}


def _generated() -> dict[str, Any]:
    return create_app().openapi()


def test_export_matches_committed_contract() -> None:
    generated = _generated()
    if os.environ.get("BUVI_UPDATE_CONTRACTS") == "1":
        CONTRACT.write_text(json.dumps(generated, indent=2, sort_keys=True) + "\n")
    assert CONTRACT.is_file(), (
        "contracts/openapi/dashboard-service.json missing: run `make contracts`"
    )
    assert json.loads(CONTRACT.read_text()) == generated, "contract drift: run `make contracts`"


def test_responses_expose_no_sql_or_result_handles() -> None:
    schemas = _generated()["components"]["schemas"]
    for name, schema in schemas.items():
        if name.endswith("Response"):
            assert not set(schema.get("properties", {})) & FORBIDDEN, name
