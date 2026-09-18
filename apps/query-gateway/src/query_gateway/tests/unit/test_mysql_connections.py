"""Phase A8: MySQL server identity and pool bookkeeping, without a server."""

from __future__ import annotations

import pytest

from query_gateway.infrastructure.connectors.mysql import ConnectionPool, supported_server


@pytest.mark.parametrize(
    ("version", "supported"),
    [
        ("8.4.3", True),
        ("8.0.36-28", True),  # Percona Server
        ("8.0.35-log", True),
        ("9.1.0", True),
        ("5.7.44", False),
        ("10.11.6-MariaDB", False),
        ("11.4.2-MariaDB-ubu2404", False),
        ("5.5.5-10.11.6-MariaDB", False),  # MariaDB's compatibility prefix
        ("8.0.11-TiDB-v7.5.0", False),
        ("", False),
    ],
)
def test_only_mysql_8_or_later_is_supported(version: str, supported: bool) -> None:
    assert supported_server(version) is supported


class _Connection:
    closed = False

    def close(self) -> None:
        self.closed = True


async def test_a_connection_released_into_a_closed_pool_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pool evicted or dropped mid-query must not keep that query's connection open."""
    connection = _Connection()

    async def _open(**_: object) -> _Connection:
        return connection

    monkeypatch.setattr(
        "query_gateway.infrastructure.connectors.mysql.open_single_statement_connection", _open
    )
    pool = ConnectionPool({}, max_size=1)
    async with pool.acquire() as acquired:
        assert acquired is connection
        pool.close()
    assert connection.closed


async def test_a_released_connection_is_reused_by_an_open_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[_Connection] = []

    async def _open(**_: object) -> _Connection:
        opened.append(_Connection())
        return opened[-1]

    monkeypatch.setattr(
        "query_gateway.infrastructure.connectors.mysql.open_single_statement_connection", _open
    )
    pool = ConnectionPool({}, max_size=1)
    for _ in range(2):
        async with pool.acquire():
            pass
    assert len(opened) == 1 and not opened[0].closed
    pool.close()
    assert opened[0].closed
