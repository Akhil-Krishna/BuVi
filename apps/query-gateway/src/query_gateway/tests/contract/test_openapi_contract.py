"""Contract test: committed `contracts/openapi/query-gateway.json` matches the code; no response
schema can carry a credential. `BUVI_UPDATE_CONTRACTS=1` re-exports it."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from query_gateway.main import create_app

pytestmark = pytest.mark.contract

CONTRACT = Path(__file__).resolve().parents[6] / "contracts" / "openapi" / "query-gateway.json"
SECRET_SHAPED = {
    "password",
    "host",
    "port",
    "username",
    "user",
    "secret",
    "secret_ref",
    "dsn",
    "sql_text",
}


def _generated() -> dict[str, Any]:
    return create_app().openapi()


def test_export_matches_committed_contract() -> None:
    generated = _generated()
    if os.environ.get("BUVI_UPDATE_CONTRACTS") == "1":
        CONTRACT.write_text(json.dumps(generated, indent=2, sort_keys=True) + "\n")
    assert CONTRACT.is_file(), "contracts/openapi/query-gateway.json missing: run `make contracts`"
    assert json.loads(CONTRACT.read_text()) == generated, "contract drift: run `make contracts`"


def test_query_endpoint_and_response_shape() -> None:
    spec = _generated()
    assert "/internal/v1/queries" in spec["paths"]
    assert not any(path.startswith("/api/") for path in spec["paths"]), (
        "no public route in Phase A4"
    )
    schemas = spec["components"]["schemas"]
    for name in ("QueryResponse", "QueryColumnResponse"):
        assert not set(schemas[name]["properties"]) & SECRET_SHAPED
    assert set(schemas["QueryRequest"]["required"]) == {"database_id", "sql", "purpose"}
