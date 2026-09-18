"""A real MySQL 8.4 loaded with the compose sample (`infra/compose/sample-sales-mysql-init`).

Used by query-gateway (execution) and metadata-service (catalog) tests, so both prove their MySQL
connectors against the exact data and read-only user the live flow uses.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pymysql

INIT_DIR = Path(__file__).resolve().parents[5] / "infra" / "compose" / "sample-sales-mysql-init"
ROOT_PASSWORD = "sales"  # noqa: S105 - compose/test throwaway
READER_USER = "buvi_reader"
READER_PASSWORD = "dev-reader-password"  # noqa: S105 - compose throwaway


@dataclass(frozen=True)
class MySqlInfo:
    host: str
    port: int

    def root(self, database: str | None = None) -> pymysql.connections.Connection:
        return pymysql.connect(
            host=self.host, port=self.port, user="root", password=ROOT_PASSWORD, database=database
        )


def sample_sales_mysql(image: str = "mysql:8.4", ready_seconds: float = 180) -> Iterator[MySqlInfo]:
    """Start the container and yield once `buvi_reader` (created last by the init script) can
    log in. Use as the body of a module- or session-scoped fixture."""
    from testcontainers.core.container import DockerContainer

    if not (INIT_DIR / "01-sales-schema.sql").is_file():  # fail fast: a bad path mounts nothing
        raise FileNotFoundError(INIT_DIR)
    container = (
        DockerContainer(image)
        .with_env("MYSQL_ROOT_PASSWORD", ROOT_PASSWORD)
        .with_command("--local-infile=0")
        .with_volume_mapping(str(INIT_DIR), "/docker-entrypoint-initdb.d", "ro")
        .with_exposed_ports(3306)
    )
    with container:
        info = MySqlInfo(container.get_container_host_ip(), int(container.get_exposed_port(3306)))
        deadline = time.monotonic() + ready_seconds
        while True:
            try:
                pymysql.connect(
                    host=info.host,
                    port=info.port,
                    user=READER_USER,
                    password=READER_PASSWORD,
                    database="sales",
                    connect_timeout=2,
                ).close()
                break
            except pymysql.MySQLError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(1)
        yield info
