"""Dashboard access and tile placement rules."""

from __future__ import annotations

import pytest

from dashboard_service.domain.policies.dashboard_policy import (
    dashboard_access,
    next_position,
    position_problems,
)

pytestmark = pytest.mark.unit


def test_access() -> None:
    assert dashboard_access(owner_id="a", visibility="private", user_id="a") == "owner"
    assert dashboard_access(owner_id="a", visibility="tenant", user_id="b") == "read"
    assert dashboard_access(owner_id="a", visibility="private", user_id="b") == "hidden"
    assert dashboard_access(owner_id="a", visibility="link", user_id="b") == "hidden"


def test_next_position_stacks_below_everything() -> None:
    assert next_position([]) == {"x": 0, "y": 0, "w": 6, "h": 4}
    placed = [{"x": 0, "y": 0, "w": 6, "h": 4}, {"x": 6, "y": 2, "w": 6, "h": 5}]
    assert next_position(placed)["y"] == 7


def test_position_bounds() -> None:
    assert position_problems({"x": 0, "y": 0, "w": 12, "h": 24}) == []
    assert position_problems({"x": 12, "y": -1, "w": 1, "h": 0}) == ["x", "w", "y", "h"]
    assert position_problems({"x": 7, "y": 0, "w": 6, "h": 4}) == ["w"]
