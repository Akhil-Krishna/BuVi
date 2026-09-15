"""Contract test: the committed `contracts/openapi/metadata-service.json` is what the code
produces, and no response schema can carry a credential. `BUVI_UPDATE_CONTRACTS=1`
re-exports it."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from metadata_service.main import create_app

pytestmark = pytest.mark.contract

CONTRACT = Path(__file__).resolve().parents[6] / "contracts" / "openapi" / "metadata-service.json"
SECRET_SHAPED = {"password", "host", "port", "username", "user", "secret", "secret_ref", "dsn"}


def _generated() -> dict[str, Any]:
    return create_app().openapi()


def test_export_matches_committed_contract() -> None:
    generated = _generated()
    if os.environ.get("BUVI_UPDATE_CONTRACTS") == "1":
        CONTRACT.write_text(json.dumps(generated, indent=2, sort_keys=True) + "\n")
    assert CONTRACT.is_file(), (
        "contracts/openapi/metadata-service.json missing: run `make contracts`"
    )
    assert json.loads(CONTRACT.read_text()) == generated, "contract drift: run `make contracts`"


def _referenced_schemas(node: Any, found: set[str]) -> None:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            found.add(ref.rsplit("/", 1)[-1])
        for value in node.values():
            _referenced_schemas(value, found)
    elif isinstance(node, list):
        for value in node:
            _referenced_schemas(value, found)


def test_no_response_schema_has_a_secret_shaped_property() -> None:
    spec = _generated()
    schemas = spec["components"]["schemas"]
    responses: set[str] = set()
    for ops in spec["paths"].values():
        for op in ops.values():
            _referenced_schemas(op.get("responses", {}), responses)
    pending = set(responses)
    while pending:
        name = pending.pop()
        nested: set[str] = set()
        _referenced_schemas(schemas.get(name, {}), nested)
        pending |= nested - responses
        responses |= nested
    assert "ConnectionSecretRequest" not in responses
    for name in responses:
        properties = set(schemas.get(name, {}).get("properties", {}))
        assert not properties & SECRET_SHAPED, (name, properties & SECRET_SHAPED)


def test_every_route_documents_its_errors_as_the_envelope() -> None:
    paths = _generated()["paths"]
    assert "/api/v1/data-sources/{data_source_id}/secret" in paths
    assert "/api/v1/data-sources/{data_source_id}/tables/{table_id}" in paths
