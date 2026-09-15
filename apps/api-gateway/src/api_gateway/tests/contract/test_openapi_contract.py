"""Contract test: the committed `contracts/openapi/api-gateway.json` is what the code
produces (Phase A2 DoD). Set `BUVI_UPDATE_CONTRACTS=1` to (re)export it."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from api_gateway.domain.catalog import CATALOG
from api_gateway.main import DEFAULT_CONTRACTS_DIR, create_app

pytestmark = pytest.mark.contract

CONTRACT = DEFAULT_CONTRACTS_DIR / "api-gateway.json"


def _generated() -> dict[str, object]:
    return create_app(contracts_dir=DEFAULT_CONTRACTS_DIR).openapi()


def test_export_matches_committed_contract() -> None:
    generated = _generated()
    rendered = json.dumps(generated, indent=2, sort_keys=True) + "\n"
    if os.environ.get("BUVI_UPDATE_CONTRACTS") == "1":
        CONTRACT.write_text(rendered)
    assert CONTRACT.is_file(), "contracts/openapi/api-gateway.json missing: run `make contracts`"
    assert json.loads(CONTRACT.read_text()) == generated, "contract drift: run `make contracts`"


def test_every_catalog_route_is_documented_with_its_path_parameters() -> None:
    paths = _generated()["paths"]
    assert isinstance(paths, dict)
    for route in CATALOG:
        op = paths[f"/api/v1{route.path}"][route.method.lower()]
        declared = {p["name"] for p in op.get("parameters", []) if p["in"] == "path"}
        assert declared == set(re.findall(r"\{([^}]+)\}", route.path)), route.path
        if route.is_stub:
            assert "501" in op["responses"] and op["x-available-in-phase"]
        if not route.public:
            assert "401" in op["responses"]


def test_error_envelope_schema_is_published() -> None:
    components = _generated()["components"]
    assert isinstance(components, dict)
    assert "ErrorResponse" in components["schemas"]


def test_identity_bodies_are_composed_from_its_contract() -> None:
    identity = Path(DEFAULT_CONTRACTS_DIR / "identity-service.json")
    if not identity.is_file():
        pytest.skip("identity-service contract not exported yet")
    paths = _generated()["paths"]
    assert isinstance(paths, dict)
    invite = paths["/api/v1/admin/invitations"]["post"]
    assert "requestBody" in invite and "201" in invite["responses"]


def test_metadata_routes_exist_in_the_metadata_contract() -> None:
    """Every live metadata-service route the gateway exposes is served by the service."""
    contract = DEFAULT_CONTRACTS_DIR / "metadata-service.json"
    if not contract.is_file():
        pytest.skip("metadata-service contract not exported yet")
    upstream = json.loads(contract.read_text())
    served = {
        (re.sub(r"\{[^}]+\}", "{}", path), method.upper())
        for path, ops in upstream["paths"].items()
        for method in ops
    }
    for route in CATALOG:
        if route.backend == "metadata-service" and not route.is_stub:
            key = (re.sub(r"\{[^}]+\}", "{}", f"/api/v1{route.path}"), route.method)
            assert key in served, key
    paths = _generated()["paths"]
    assert isinstance(paths, dict)
    create = paths["/api/v1/data-sources"]["post"]
    assert "requestBody" in create and "201" in create["responses"]
