"""One executor per run: a session-level Postgres advisory lock held for the whole execution.

A second `execute` for the same run (a redelivered queue message while the first is still
running) gets `acquired=False` instead of running the Flow twice. If the process dies, its
connection closes and Postgres releases the lock -- the redelivery can then resume the run.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

_LOCK_SQL = text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))")
_UNLOCK_SQL = text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))")


def run_lock(engine: AsyncEngine) -> Callable[[uuid.UUID], AbstractAsyncContextManager[bool]]:
    @asynccontextmanager
    async def lock(run_id: uuid.UUID) -> AsyncIterator[bool]:
        key = f"analytics-run:{run_id}"
        async with engine.connect() as connection:
            acquired = bool(await connection.scalar(_LOCK_SQL, {"key": key}))
            await connection.commit()
            try:
                yield acquired
            finally:
                if acquired:
                    await connection.execute(_UNLOCK_SQL, {"key": key})
                    await connection.commit()

    return lock
