"""Data access for the `metadata` schema.

Every query filters on `tenant_id` explicitly -- the application-layer half of Section
19's two-layer isolation; the RLS policies are the other half, and neither substitutes
for the other. The one read without a tenant filter is `get_data_source_tenant_id`,
the Section 7.2 loader, whose whole purpose is to return the owning tenant so the
caller can compare it (and which RLS still confines to the caller's tenant).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, delete, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from metadata_service.domain.value_objects.catalog import IntrospectedCatalog
from metadata_service.infrastructure.db.models import (
    CatalogColumn,
    CatalogTable,
    DataSource,
    Relationship,
    SchemaSnapshot,
)

_CHUNK = 1000


def _chunks[T](items: Sequence[T], size: int = _CHUNK) -> Iterator[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


@dataclass(frozen=True)
class CatalogCounts:
    tables: int
    columns: int
    relationships: int


@dataclass(frozen=True)
class ColumnRef:
    column_id: uuid.UUID
    table_id: uuid.UUID
    schema_name: str
    table_name: str
    column_name: str


@dataclass(frozen=True)
class RelationshipRow:
    id: uuid.UUID
    relationship_type: str
    from_column: ColumnRef
    to_column: ColumnRef


class MetadataRepository:
    """Repository over the `metadata` schema, scoped to one tenant-bound session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    # --- data sources -------------------------------------------------------------

    async def add_data_source(self, data_source: DataSource) -> DataSource:
        self._session.add(data_source)
        await self._session.flush()
        await self._session.refresh(data_source)
        return data_source

    async def get_data_source(
        self, tenant_id: uuid.UUID, data_source_id: uuid.UUID, *, for_update: bool = False
    ) -> DataSource | None:
        statement = select(DataSource).where(
            DataSource.tenant_id == tenant_id, DataSource.id == data_source_id
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement.execution_options(populate_existing=True))
        return result.scalar_one_or_none()

    async def get_data_source_tenant_id(self, data_source_id: uuid.UUID) -> uuid.UUID | None:
        result = await self._session.execute(
            select(DataSource.tenant_id).where(DataSource.id == data_source_id)
        )
        return result.scalar_one_or_none()

    async def list_data_sources(
        self, tenant_id: uuid.UUID, *, limit: int, cursor: uuid.UUID | None
    ) -> tuple[list[DataSource], uuid.UUID | None]:
        """Keyset pagination on `id`, stable under concurrent inserts."""
        statement: Select[tuple[DataSource]] = (
            select(DataSource)
            .where(DataSource.tenant_id == tenant_id)
            .order_by(DataSource.id)
            .limit(limit + 1)
        )
        if cursor is not None:
            statement = statement.where(DataSource.id > cursor)
        rows = list((await self._session.execute(statement)).scalars().all())
        if len(rows) > limit:
            return rows[:limit], rows[limit - 1].id
        return rows, None

    async def set_status(
        self,
        data_source: DataSource,
        *,
        status: str,
        synced_at: dt.datetime | None = None,
    ) -> DataSource:
        data_source.status = status
        if synced_at is not None:
            data_source.last_sync_at = synced_at
        await self._session.flush()
        await self._session.refresh(data_source)
        return data_source

    # --- catalog reads --------------------------------------------------------------

    @staticmethod
    def _column_count() -> Any:
        return (
            select(func.count(CatalogColumn.id))
            .where(CatalogColumn.table_id == CatalogTable.id)
            .correlate(CatalogTable)
            .scalar_subquery()
        )

    async def list_tables(
        self,
        tenant_id: uuid.UUID,
        data_source_id: uuid.UUID,
        *,
        limit: int,
        after: tuple[str, str] | None,
    ) -> tuple[list[tuple[CatalogTable, int]], tuple[str, str] | None]:
        """Keyset pagination on (schema_name, table_name)."""
        statement = (
            select(CatalogTable, self._column_count())
            .where(
                CatalogTable.tenant_id == tenant_id,
                CatalogTable.data_source_id == data_source_id,
            )
            .order_by(CatalogTable.schema_name, CatalogTable.table_name)
            .limit(limit + 1)
        )
        if after is not None:
            statement = statement.where(
                tuple_(CatalogTable.schema_name, CatalogTable.table_name) > tuple_(*after)
            )
        rows = [(row[0], int(row[1])) for row in (await self._session.execute(statement)).all()]
        if len(rows) > limit:
            rows = rows[:limit]
            last = rows[-1][0]
            return rows, (last.schema_name, last.table_name)
        return rows, None

    async def get_table(
        self, tenant_id: uuid.UUID, data_source_id: uuid.UUID, table_id: uuid.UUID
    ) -> tuple[CatalogTable, int] | None:
        statement = select(CatalogTable, self._column_count()).where(
            CatalogTable.tenant_id == tenant_id,
            CatalogTable.data_source_id == data_source_id,
            CatalogTable.id == table_id,
        )
        row = (await self._session.execute(statement)).first()
        return (row[0], int(row[1])) if row else None

    async def list_catalog(
        self, tenant_id: uuid.UUID, data_source_id: uuid.UUID
    ) -> list[tuple[CatalogTable, list[CatalogColumn]]]:
        """Every catalogued table of a data source with its columns, for query policy."""
        tables = list(
            (
                await self._session.execute(
                    select(CatalogTable)
                    .where(
                        CatalogTable.tenant_id == tenant_id,
                        CatalogTable.data_source_id == data_source_id,
                    )
                    .order_by(CatalogTable.schema_name, CatalogTable.table_name)
                )
            )
            .scalars()
            .all()
        )
        if not tables:
            return []
        columns = (
            (
                await self._session.execute(
                    select(CatalogColumn)
                    .where(CatalogColumn.table_id.in_([t.id for t in tables]))
                    .order_by(CatalogColumn.column_name)
                )
            )
            .scalars()
            .all()
        )
        by_table: dict[uuid.UUID, list[CatalogColumn]] = {t.id: [] for t in tables}
        for column in columns:
            by_table[column.table_id].append(column)
        return [(table, by_table[table.id]) for table in tables]

    async def list_columns(self, table_id: uuid.UUID) -> list[CatalogColumn]:
        result = await self._session.execute(
            select(CatalogColumn)
            .where(CatalogColumn.table_id == table_id)
            .order_by(CatalogColumn.column_name)
        )
        return list(result.scalars().all())

    async def list_relationships_for_table(
        self, tenant_id: uuid.UUID, table_id: uuid.UUID
    ) -> list[RelationshipRow]:
        from_col, to_col = aliased(CatalogColumn), aliased(CatalogColumn)
        from_tab, to_tab = aliased(CatalogTable), aliased(CatalogTable)
        statement = (
            select(
                Relationship.id,
                Relationship.relationship_type,
                from_col.id,
                from_tab.id,
                from_tab.schema_name,
                from_tab.table_name,
                from_col.column_name,
                to_col.id,
                to_tab.id,
                to_tab.schema_name,
                to_tab.table_name,
                to_col.column_name,
            )
            .join(from_col, from_col.id == Relationship.from_column_id)
            .join(from_tab, from_tab.id == from_col.table_id)
            .join(to_col, to_col.id == Relationship.to_column_id)
            .join(to_tab, to_tab.id == to_col.table_id)
            .where(
                Relationship.tenant_id == tenant_id,
                or_(from_tab.id == table_id, to_tab.id == table_id),
            )
            .order_by(
                from_tab.schema_name,
                from_tab.table_name,
                from_col.column_name,
                to_tab.schema_name,
                to_tab.table_name,
                to_col.column_name,
            )
        )
        return [
            RelationshipRow(
                id=row[0],
                relationship_type=row[1],
                from_column=ColumnRef(row[2], row[3], row[4], row[5], row[6]),
                to_column=ColumnRef(row[7], row[8], row[9], row[10], row[11]),
            )
            for row in (await self._session.execute(statement)).all()
        ]

    # --- catalog sync ----------------------------------------------------------------

    async def apply_catalog(
        self, tenant_id: uuid.UUID, data_source_id: uuid.UUID, catalog: IntrospectedCatalog
    ) -> CatalogCounts:
        """Make the stored catalog match `catalog`, in the caller's transaction.

        Diff-based rather than delete-and-reinsert, so table and column ids -- which
        the semantic layer (Section 8.3) stores as references -- survive a re-sync, as
        do the fields sync does not own (`is_visible_to_agent`, `is_pii`).
        """
        tables = CatalogTable.__table__
        columns = CatalogColumn.__table__
        relationships = Relationship.__table__

        source_column_ids = (
            select(columns.c.id)
            .join(tables, tables.c.id == columns.c.table_id)
            .where(tables.c.data_source_id == data_source_id)
        )
        # Foreign keys are rebuilt from the source; inferred relationships are kept.
        await self._session.execute(
            delete(relationships).where(
                relationships.c.relationship_type == "fk",
                or_(
                    relationships.c.from_column_id.in_(source_column_ids),
                    relationships.c.to_column_id.in_(source_column_ids),
                ),
            )
        )

        wanted_tables = {(t.schema_name, t.table_name) for t in catalog.tables}
        existing_tables = (
            await self._session.execute(
                select(tables.c.id, tables.c.schema_name, tables.c.table_name).where(
                    tables.c.data_source_id == data_source_id
                )
            )
        ).all()
        stale_tables = [
            row.id
            for row in existing_tables
            if (row.schema_name, row.table_name) not in wanted_tables
        ]
        for chunk in _chunks(stale_tables):
            await self._session.execute(delete(tables).where(tables.c.id.in_(chunk)))

        table_ids: dict[tuple[str, str], uuid.UUID] = {}
        for chunk in _chunks(catalog.tables):
            statement = insert(tables).values(
                [
                    {
                        "tenant_id": tenant_id,
                        "data_source_id": data_source_id,
                        "schema_name": t.schema_name,
                        "table_name": t.table_name,
                        "description": t.description,
                        "row_count_estimate": t.row_count_estimate,
                    }
                    for t in chunk
                ]
            )
            statement = statement.on_conflict_do_update(
                constraint="tables_data_source_id_schema_name_table_name_key",
                set_={
                    "description": statement.excluded.description,
                    "row_count_estimate": statement.excluded.row_count_estimate,
                },
            ).returning(tables.c.id, tables.c.schema_name, tables.c.table_name)
            for row in (await self._session.execute(statement)).all():
                table_ids[(row.schema_name, row.table_name)] = row.id

        column_rows = [
            {
                "table_id": table_ids[(t.schema_name, t.table_name)],
                "column_name": c.name,
                "data_type": c.data_type,
                "description": c.description,
            }
            for t in catalog.tables
            for c in t.columns
        ]
        wanted_columns = {(r["table_id"], r["column_name"]) for r in column_rows}
        existing_columns = (
            await self._session.execute(
                select(columns.c.id, columns.c.table_id, columns.c.column_name)
                .join(tables, tables.c.id == columns.c.table_id)
                .where(tables.c.data_source_id == data_source_id)
            )
        ).all()
        stale_columns = [
            row.id
            for row in existing_columns
            if (row.table_id, row.column_name) not in wanted_columns
        ]
        for chunk in _chunks(stale_columns):
            await self._session.execute(delete(columns).where(columns.c.id.in_(chunk)))

        column_ids: dict[tuple[uuid.UUID, str], uuid.UUID] = {}
        for chunk in _chunks(column_rows):
            statement = insert(columns).values(list(chunk))
            statement = statement.on_conflict_do_update(
                constraint="columns_table_id_column_name_key",
                set_={
                    "data_type": statement.excluded.data_type,
                    "description": statement.excluded.description,
                },
            ).returning(columns.c.id, columns.c.table_id, columns.c.column_name)
            for row in (await self._session.execute(statement)).all():
                column_ids[(row.table_id, row.column_name)] = row.id

        relationship_rows: list[dict[str, Any]] = []
        for fk in catalog.foreign_keys:
            from_table = table_ids.get((fk.from_schema, fk.from_table))
            to_table = table_ids.get((fk.to_schema, fk.to_table))
            if from_table is None or to_table is None:
                continue
            from_column = column_ids.get((from_table, fk.from_column))
            to_column = column_ids.get((to_table, fk.to_column))
            if from_column is None or to_column is None:
                continue
            relationship_rows.append(
                {
                    "tenant_id": tenant_id,
                    "from_column_id": from_column,
                    "to_column_id": to_column,
                    "relationship_type": "fk",
                }
            )
        for chunk in _chunks(relationship_rows):
            await self._session.execute(insert(relationships).values(list(chunk)))

        return CatalogCounts(
            tables=len(table_ids), columns=len(column_ids), relationships=len(relationship_rows)
        )

    async def add_snapshot(
        self, tenant_id: uuid.UUID, data_source_id: uuid.UUID, checksum: str
    ) -> SchemaSnapshot:
        snapshot = SchemaSnapshot(
            tenant_id=tenant_id, data_source_id=data_source_id, checksum=checksum
        )
        self._session.add(snapshot)
        await self._session.flush()
        await self._session.refresh(snapshot)
        return snapshot
