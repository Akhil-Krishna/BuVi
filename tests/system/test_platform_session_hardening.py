"""Phase C1 entry requirement: the platform database's own sessions fail fast (ADR 0021).

ADR 0014 named the gap: the Phase A5 crash-resume stall's failure class is a client that vanished
without a FIN leaving its backend idle in transaction, holding row locks until TCP keepalive
detection -- 2h+ on Linux by default. Dev and CI had `idle_in_transaction_session_timeout=60s` in
compose; production had nothing, because there is no Terraform to put it in.

These tests assert the fix is carried by the application instead, so it is true in every
environment: every engine sends the hardening as asyncpg startup parameters, and a waiter blocked
on a lock a dead holder never released fails fast rather than blocking until its stage timeout.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

import asyncpg
import pytest
from identity_service.core.config import Settings
from identity_service.infrastructure.db.session import (
    PLATFORM_SESSION_SETTINGS,
    create_engine,
)
from sqlalchemy import text

pytestmark = [pytest.mark.system, pytest.mark.security, pytest.mark.asyncio]

#: Arbitrary, test-owned advisory lock key. Advisory locks keep this test off every real table:
#: `lock_timeout` covers "a table, index, row, or other database object" alike, and nothing here
#: has to write, so a failure can never leave tenant data behind.
LOCK_KEY = 918_273_645

#: Short enough to keep the behavioural tests quick. The production value is asserted separately,
#: from the constant itself, so a fast test never hides a wrong shipped setting.
FAST_TIMEOUT = "1s"


def _dsn() -> str:
    """The app-path DSN as asyncpg wants it (SQLAlchemy's `+asyncpg` is not a libpq scheme)."""
    return str(Settings().database_dsn).replace("postgresql+asyncpg://", "postgresql://")


async def test_every_platform_engine_ships_the_hardening() -> None:
    """The real `create_engine` -- not a hand-built connection -- applies all five settings."""
    engine = create_engine(Settings())
    try:
        async with engine.connect() as connection:
            for guc, expected in PLATFORM_SESSION_SETTINGS.items():
                actual = await connection.scalar(text(f"SHOW {guc}"))
                # Postgres normalises "60s" to "1min"; compare as an interval, not as a string.
                if guc.startswith("tcp_keepalives"):
                    assert actual == expected, f"{guc}: {actual!r} != {expected!r}"
                else:
                    # Cast through text explicitly: with a bare `CAST(:p AS interval)` asyncpg
                    # infers `interval` for the parameter and refuses to encode a str.
                    same = await connection.scalar(
                        text(
                            "SELECT CAST(CAST(:actual AS text) AS interval)"
                            " = CAST(CAST(:expected AS text) AS interval)"
                        ),
                        {"actual": actual, "expected": expected},
                    )
                    assert same is True, f"{guc}: {actual!r} != {expected!r}"
    finally:
        await engine.dispose()


async def test_the_shipped_lock_timeout_is_finite_and_below_a_stage_timeout() -> None:
    """A `lock_timeout` of 0 (the Postgres default) is what let the A5 stall run to its stage
    timeout. Assert the shipped value is both set and meaningfully shorter than that."""
    value = PLATFORM_SESSION_SETTINGS["lock_timeout"]
    assert value not in ("0", "0s", ""), "lock_timeout must be finite (0 means wait forever)"
    probe = await asyncpg.connect(_dsn())
    try:
        seconds = await probe.fetchval(
            "SELECT EXTRACT(EPOCH FROM CAST(CAST($1 AS text) AS interval))", value
        )
    finally:
        await probe.close()
    assert 0 < seconds <= 60, f"lock_timeout {value} is not a fail-fast value"


async def test_a_waiter_fails_fast_instead_of_hanging() -> None:
    """The behaviour the entry requirement asks for: a blocked waiter gives up, with a lock
    error naming the wait, rather than hanging until something further up times out."""
    holder = await asyncpg.connect(_dsn())
    waiter = await asyncpg.connect(_dsn(), server_settings={"lock_timeout": FAST_TIMEOUT})
    try:
        await holder.execute("BEGIN")
        await holder.execute("SELECT pg_advisory_xact_lock($1)", LOCK_KEY)

        started = time.monotonic()
        with pytest.raises(asyncpg.exceptions.LockNotAvailableError):
            await waiter.execute("SELECT pg_advisory_xact_lock($1)", LOCK_KEY)
        elapsed = time.monotonic() - started

        # Fails fast, and demonstrably *because* of the timeout rather than by chance.
        assert elapsed < 10, f"waiter took {elapsed:.1f}s -- that is hanging, not failing fast"
        assert elapsed >= 0.5, f"waiter returned in {elapsed:.1f}s -- it never actually waited"
    finally:
        await holder.close()
        await waiter.close()


async def test_killing_the_lock_holder_frees_the_waiter() -> None:
    """The A5 scenario itself: the holder dies mid-transaction and the waiter then proceeds,
    rather than inheriting a lock nobody will ever release."""
    holder = await asyncpg.connect(_dsn())
    waiter = await asyncpg.connect(_dsn(), server_settings={"lock_timeout": "30s"})
    try:
        await holder.execute("BEGIN")
        await holder.execute("SELECT pg_advisory_xact_lock($1)", LOCK_KEY)
        holder_pid = holder.get_server_pid()

        blocked = asyncio.create_task(waiter.execute("SELECT pg_advisory_xact_lock($1)", LOCK_KEY))
        await asyncio.sleep(1)
        assert not blocked.done(), "the waiter should still be blocked while the holder lives"

        killer = await asyncpg.connect(_dsn())
        try:
            await killer.execute("SELECT pg_terminate_backend($1)", holder_pid)
        finally:
            await killer.close()

        # The lock dies with the backend that held it, so this resolves without the timeout.
        await asyncio.wait_for(blocked, timeout=20)
    finally:
        await waiter.close()
        # The holder was deliberately terminated above, so closing it may well raise.
        with contextlib.suppress(Exception):
            await holder.close(timeout=2)
