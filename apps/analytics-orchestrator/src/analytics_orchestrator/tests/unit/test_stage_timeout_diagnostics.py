"""A stage timeout names where the stage was stuck (ADR 0015), so an intermittent stall is
diagnosable from one log line instead of a reproduction."""

from __future__ import annotations

import asyncio

import pytest

from analytics_orchestrator.application.services.run_executor import _awaiting

pytestmark = pytest.mark.unit


async def _stuck_on_a_lock() -> None:
    await asyncio.Event().wait()


async def test_the_timeout_reports_the_awaiting_frame() -> None:
    try:
        async with asyncio.timeout(0.01):
            await _stuck_on_a_lock()
    except TimeoutError as timeout:
        frames = _awaiting(timeout)
    assert any(frame.endswith(":_stuck_on_a_lock") for frame in frames), frames
    assert all(frame.count(":") == 2 for frame in frames)  # file:line:function, nothing else
