"""The connector contract (Sections 4.1, 13.1; Phase A8 adds engines behind it).

A connector does exactly two things for metadata-service: a bounded connectivity
check, and catalog introspection. It never runs caller-supplied SQL -- query execution
belongs to query-gateway (Section 13) -- and it never lets a driver's error text out:
failures surface as `ConnectorError(code)` or a `ConnectivityResult` with a code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from metadata_service.domain.value_objects.catalog import IntrospectedCatalog
from metadata_service.domain.value_objects.connection import ConnectionTarget
from metadata_service.domain.value_objects.diagnostics import ConnectivityResult


@dataclass(frozen=True)
class ConnectorLimits:
    connect_timeout_seconds: float
    statement_timeout_ms: int
    max_tables: int
    max_columns: int


class CatalogConnector(Protocol):
    async def test(self, target: ConnectionTarget) -> ConnectivityResult:
        """Connect, count visible tables, disconnect. Never raises for a database fault."""
        ...

    async def introspect(self, target: ConnectionTarget) -> IntrospectedCatalog:
        """Read tables, columns and foreign keys. Raises `ConnectorError` on failure."""
        ...
