"""Who may see or change a dashboard, and where a new tile goes (Sections 7.2, 9)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Final, Literal

GRID_COLUMNS: Final = 12
MAX_ROW: Final = 1_000
MAX_HEIGHT: Final = 24
DEFAULT_TILE: Final = {"w": 6, "h": 4}

Access = Literal["hidden", "read", "owner"]


def dashboard_access(*, owner_id: str, visibility: str, user_id: str) -> Access:
    """The owner can change it; a `tenant` dashboard is readable tenant-wide; anything else is
    hidden -- answered `404` like another tenant's (`link` sharing is Phase A10)."""
    if owner_id == user_id:
        return "owner"
    if visibility == "tenant":
        return "read"
    return "hidden"


def next_position(existing: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """New tiles go on a fresh row below everything, full default size."""
    bottom = max((int(p["y"]) + int(p["h"]) for p in existing), default=0)
    return {"x": 0, "y": min(bottom, MAX_ROW), **DEFAULT_TILE}


def position_problems(position: Mapping[str, int]) -> list[str]:
    x, y, w, h = position["x"], position["y"], position["w"], position["h"]
    problems = []
    if not 0 <= x < GRID_COLUMNS:
        problems.append("x")
    if not 1 <= w <= GRID_COLUMNS or x + w > GRID_COLUMNS:
        problems.append("w")
    if not 0 <= y <= MAX_ROW:
        problems.append("y")
    if not 1 <= h <= MAX_HEIGHT:
        problems.append("h")
    return problems
