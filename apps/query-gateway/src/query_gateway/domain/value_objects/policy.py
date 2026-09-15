"""The connection policy a query is validated against (Sections 12, 13).

Loaded from metadata-service's catalog for one data source. It is the identifier allow-list:
a query may reference only these tables and columns ("prevents blind schema probing beyond
what the catalog already exposes", Section 13).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum


class Purpose(StrEnum):
    """Section 8.5 `query_executions.purpose`."""

    ANALYTICS_RUN = "analytics_run"
    SQL_EDITOR = "sql_editor"
    EXPORT = "export"


@dataclass(frozen=True)
class ColumnPolicy:
    name: str
    data_type: str
    is_pii: bool = False


@dataclass(frozen=True)
class TablePolicy:
    schema_name: str
    table_name: str
    columns: tuple[ColumnPolicy, ...]
    is_visible_to_agent: bool = True

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"

    def column(self, name: str) -> ColumnPolicy | None:
        return next((c for c in self.columns if c.name == name), None)


@dataclass(frozen=True)
class DataSourcePolicy:
    data_source_id: uuid.UUID
    tenant_id: uuid.UUID
    engine: str
    database_name: str
    allowed_schemas: tuple[str, ...]
    status: str
    secret_ref: str
    tables: tuple[TablePolicy, ...]
    _index: dict[tuple[str, str], TablePolicy] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        for table in self.tables:
            if table.schema_name in self.allowed_schemas:
                self._index[(table.schema_name, table.table_name)] = table

    def table(self, schema_name: str, table_name: str) -> TablePolicy | None:
        """A catalogued table inside `allowed_schemas`, by exact (normalized) name."""
        return self._index.get((schema_name, table_name))

    def tables_named(self, table_name: str) -> list[TablePolicy]:
        """Candidates for an unqualified reference, in `allowed_schemas` order."""
        return [
            t
            for schema in self.allowed_schemas
            if (t := self._index.get((schema, table_name))) is not None
        ]
