"""The introspected shape of a data source's catalog (Sections 8.2, 12).

Produced by a connector, consumed by the sync job. Only structure and the source's own
comments -- never row data: `sample_values` and PII profiling belong to the profiling
job in worker-runtime, not to schema sync.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class IntrospectedColumn:
    name: str
    data_type: str
    description: str | None = None


@dataclass(frozen=True)
class IntrospectedTable:
    schema_name: str
    table_name: str
    columns: tuple[IntrospectedColumn, ...]
    description: str | None = None
    row_count_estimate: int | None = None


@dataclass(frozen=True)
class IntrospectedForeignKey:
    from_schema: str
    from_table: str
    from_column: str
    to_schema: str
    to_table: str
    to_column: str


@dataclass(frozen=True)
class IntrospectedCatalog:
    tables: tuple[IntrospectedTable, ...]
    foreign_keys: tuple[IntrospectedForeignKey, ...]

    @property
    def column_count(self) -> int:
        return sum(len(table.columns) for table in self.tables)

    def checksum(self) -> str:
        """SHA-256 of the catalog's structure (`schema_snapshots.checksum`).

        Canonical and order-independent, and excludes row-count estimates, which drift
        with every ANALYZE: two syncs of an unchanged schema produce the same checksum.
        """
        canonical = {
            "tables": sorted(
                [
                    table.schema_name,
                    table.table_name,
                    table.description,
                    sorted([c.name, c.data_type, c.description] for c in table.columns),
                ]
                for table in self.tables
            ),
            "foreign_keys": sorted(
                [
                    fk.from_schema,
                    fk.from_table,
                    fk.from_column,
                    fk.to_schema,
                    fk.to_table,
                    fk.to_column,
                ]
                for fk in self.foreign_keys
            ),
        }
        encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
