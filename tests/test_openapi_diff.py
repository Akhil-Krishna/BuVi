"""The contract diff (Section 26): breaking changes need a major bump; stubs promise nothing."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

pytestmark = pytest.mark.contract

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "openapi_diff.py"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("openapi_diff", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _doc(op: dict[str, Any]) -> dict[str, Any]:
    return {"info": {"version": "1.0.0"}, "paths": {"/api/v1/things": {"post": op}}}


STUB = {"responses": {"501": {}}, "x-available-in-phase": "A3"}
LIVE = {"requestBody": {"required": True}, "responses": {"201": {}}}


def test_replacing_a_stub_with_a_real_contract_is_not_breaking() -> None:
    assert _module().breaking_changes(_doc(STUB), _doc(LIVE)) == []


def test_making_a_live_body_required_is_breaking() -> None:
    old = {"responses": {"201": {}}}
    assert _module().breaking_changes(_doc(old), _doc(LIVE)) == [
        "POST /api/v1/things: request body became required"
    ]


def test_removing_a_stub_operation_is_still_breaking() -> None:
    new: dict[str, Any] = {"info": {"version": "1.0.0"}, "paths": {}}
    assert _module().breaking_changes(_doc(STUB), new) == ["removed operation POST /api/v1/things"]


def test_removed_success_response_is_breaking() -> None:
    old = {"responses": {"200": {}, "201": {}}}
    new = {"responses": {"201": {}}}
    assert _module().breaking_changes(_doc(old), _doc(new)) == [
        "POST /api/v1/things: removed success response 200"
    ]
