"""SQLAlchemy mapping of the `metadata` schema (Section 8.2).

A transcription of the Section 8.2 DDL: names, types, nullability, checks, defaults
and unique constraints match it and migration `0001_metadata_schema`, which adds the
RLS policies, the `updated_at` trigger and the list-query indexes on top.

Nothing here holds a secret: `data_sources.secret_ref` is a Vault path.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "metadata"

_NOW = text("now()")
_GEN_UUID = text("gen_random_uuid()")


class Base(DeclarativeBase):
    """Declarative base scoped to the `metadata` schema."""


class DataSource(Base):
    __tablename__ = "data_sources"
    __table_args__ = (
        CheckConstraint(
            "engine IN ('postgres','mysql','snowflake','bigquery','redshift')",
            name="ck_data_sources_engine",
        ),
        CheckConstraint(
            "status IN ('pending','active','error','disabled')", name="ck_data_sources_status"
        ),
        Index("idx_datasources_tenant", "tenant_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=_GEN_UUID
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    engine: Mapped[str] = mapped_column(Text, nullable=False)
    #: Sanitized display value only, never a DSN.
    host_label: Mapped[str] = mapped_column(Text, nullable=False)
    database_name: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_schemas: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    capabilities: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    #: Vault path, e.g. `secret/data/tenants/<id>/datasources/<id>`.
    secret_ref: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    last_sync_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class SchemaSnapshot(Base):
    __tablename__ = "schema_snapshots"
    __table_args__ = (
        Index("idx_schema_snapshots_tenant_source", "tenant_id", "data_source_id", "snapshot_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=_GEN_UUID
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.data_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    snapshot_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    checksum: Mapped[str] = mapped_column(Text, nullable=False)


class CatalogTable(Base):
    """`metadata.tables`. Named to avoid shadowing SQLAlchemy's `Table`."""

    __tablename__ = "tables"
    __table_args__ = (
        UniqueConstraint(
            "data_source_id",
            "schema_name",
            "table_name",
            name="tables_data_source_id_schema_name_table_name_key",
        ),
        Index("idx_tables_tenant_source", "tenant_id", "data_source_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=_GEN_UUID
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.data_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    schema_name: Mapped[str] = mapped_column(Text, nullable=False)
    table_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    row_count_estimate: Mapped[int | None] = mapped_column(BigInteger)
    is_visible_to_agent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )


class CatalogColumn(Base):
    """`metadata.columns`. No `tenant_id` in Section 8.2: tenancy is its table's."""

    __tablename__ = "columns"
    __table_args__ = (
        UniqueConstraint("table_id", "column_name", name="columns_table_id_column_name_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=_GEN_UUID
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tables.id", ondelete="CASCADE"), nullable=False
    )
    column_name: Mapped[str] = mapped_column(Text, nullable=False)
    data_type: Mapped[str] = mapped_column(Text, nullable=False)
    is_pii: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    description: Mapped[str | None] = mapped_column(Text)
    sample_values: Mapped[Any | None] = mapped_column(JSONB)


class Relationship(Base):
    __tablename__ = "relationships"
    __table_args__ = (
        CheckConstraint(
            "relationship_type IN ('fk','inferred')", name="ck_relationships_relationship_type"
        ),
        Index("idx_relationships_tenant", "tenant_id"),
        Index("idx_relationships_from_column", "from_column_id"),
        Index("idx_relationships_to_column", "to_column_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=_GEN_UUID
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # ON DELETE CASCADE is an ADR 0004 correction to the Section 8.2 DDL.
    from_column_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.columns.id", ondelete="CASCADE"), nullable=False
    )
    to_column_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.columns.id", ondelete="CASCADE"), nullable=False
    )
    relationship_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'fk'")
    )


class DataSourceGrant(Base):
    """A per-connection `sql:execute` grant (Section 7.1; Phase A10). `org_admin` needs none."""

    __tablename__ = "data_source_grants"
    __table_args__ = (
        UniqueConstraint(
            "data_source_id", "user_id", name="data_source_grants_data_source_id_user_id_key"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=_GEN_UUID
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.data_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    granted_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    granted_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
