"""`system` tests run against the live compose stack (scripts/backend-e2e.sh sets BUVI_SYSTEM=1).
Without it they are skipped; with it, an unreachable stack is a failure, never a skip."""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if os.environ.get("BUVI_SYSTEM") == "1":
        return
    skip = pytest.mark.skip(reason="needs the live stack: run scripts/backend-e2e.sh")
    for item in items:
        if item.get_closest_marker("system"):
            item.add_marker(skip)
