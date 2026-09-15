"""metadata schema: data sources, schema snapshots, tables, columns, relationships

Implements the Section 8.2 DDL, plus the cross-cutting rules Section 8's preamble
and Section 19 attach to it:

* `updated_at` maintained by trigger (only `data_sources` has the column in 8.2);
* an index leading with `tenant_id` for every list query;
* Row-Level Security on every table, as defense in depth behind the
  application-layer tenant filters. `columns` carries no `tenant_id` in the DDL,
  so its policy scopes it through its (RLS-protected) table;
* `relationships` foreign keys cascade (ADR 0004): the 8.2 DDL cascades
  `data_sources -> tables -> columns`, but a non-cascading reference from
  `relationships` would make that cascade fail as soon as one FK was catalogued.

Revision ID: 0001_metadata_schema
Revises:
Create Date: 2026-09-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_metadata_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "metadata"
APP_ROLE = "buvi_app"

#: Tables carrying `tenant_id` directly (Section 19).
TENANT_OWNED = ("data_sources", "schema_snapshots", "tables", "relationships")

_UUID = postgresql.UUID(as_uuid=True)
_GEN_UUID = sa.text("gen_random_uuid()")
_NOW = sa.text("now()")


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    op.create_table(
        "data_sources",
        sa.Column("id", _UUID, server_default=_GEN_UUID, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("engine", sa.Text(), nullable=False),
        sa.Column("host_label", sa.Text(), nullable=False),
        sa.Column("database_name", sa.Text(), nullable=False),
        sa.Column(
            "allowed_schemas",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "capabilities", postgresql.JSONB(), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("secret_ref", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", _UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint(
            "engine IN ('postgres','mysql','snowflake','bigquery','redshift')",
            name="ck_data_sources_engine",
        ),
        sa.CheckConstraint(
            "status IN ('pending','active','error','disabled')", name="ck_data_sources_status"
        ),
        sa.PrimaryKeyConstraint("id", name="data_sources_pkey"),
        schema=SCHEMA,
    )
    op.create_index("idx_datasources_tenant", "data_sources", ["tenant_id"], schema=SCHEMA)

    op.create_table(
        "schema_snapshots",
        sa.Column("id", _UUID, server_default=_GEN_UUID, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("data_source_id", _UUID, nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("checksum", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["data_source_id"],
            [f"{SCHEMA}.data_sources.id"],
            name="schema_snapshots_data_source_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="schema_snapshots_pkey"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_schema_snapshots_tenant_source",
        "schema_snapshots",
        ["tenant_id", "data_source_id", "snapshot_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "tables",
        sa.Column("id", _UUID, server_default=_GEN_UUID, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("data_source_id", _UUID, nullable=False),
        sa.Column("schema_name", sa.Text(), nullable=False),
        sa.Column("table_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("row_count_estimate", sa.BigInteger(), nullable=True),
        sa.Column(
            "is_visible_to_agent", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["data_source_id"],
            [f"{SCHEMA}.data_sources.id"],
            name="tables_data_source_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="tables_pkey"),
        sa.UniqueConstraint(
            "data_source_id",
            "schema_name",
            "table_name",
            name="tables_data_source_id_schema_name_table_name_key",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_tables_tenant_source", "tables", ["tenant_id", "data_source_id"], schema=SCHEMA
    )

    op.create_table(
        "columns",
        sa.Column("id", _UUID, server_default=_GEN_UUID, nullable=False),
        sa.Column("table_id", _UUID, nullable=False),
        sa.Column("column_name", sa.Text(), nullable=False),
        sa.Column("data_type", sa.Text(), nullable=False),
        sa.Column("is_pii", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sample_values", postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(
            ["table_id"],
            [f"{SCHEMA}.tables.id"],
            name="columns_table_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="columns_pkey"),
        sa.UniqueConstraint("table_id", "column_name", name="columns_table_id_column_name_key"),
        schema=SCHEMA,
    )

    op.create_table(
        "relationships",
        sa.Column("id", _UUID, server_default=_GEN_UUID, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("from_column_id", _UUID, nullable=False),
        sa.Column("to_column_id", _UUID, nullable=False),
        sa.Column(
            "relationship_type", sa.Text(), server_default=sa.text("'fk'"), nullable=False
        ),
        sa.CheckConstraint(
            "relationship_type IN ('fk','inferred')", name="ck_relationships_relationship_type"
        ),
        sa.ForeignKeyConstraint(
            ["from_column_id"],
            [f"{SCHEMA}.columns.id"],
            name="relationships_from_column_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["to_column_id"],
            [f"{SCHEMA}.columns.id"],
            name="relationships_to_column_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="relationships_pkey"),
        schema=SCHEMA,
    )
    op.create_index("idx_relationships_tenant", "relationships", ["tenant_id"], schema=SCHEMA)
    op.create_index(
        "idx_relationships_from_column", "relationships", ["from_column_id"], schema=SCHEMA
    )
    op.create_index("idx_relationships_to_column", "relationships", ["to_column_id"], schema=SCHEMA)

    _install_updated_at_trigger()
    _enable_row_level_security()
    _grant_app_role()


def _install_updated_at_trigger() -> None:
    """Section 8: `updated_at` is maintained by trigger, not by the ORM."""
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.set_updated_at() RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        f"CREATE TRIGGER trg_data_sources_updated_at BEFORE UPDATE ON {SCHEMA}.data_sources "
        f"FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.set_updated_at()"
    )


def _enable_row_level_security() -> None:
    """Section 19: RLS on every table. An unset `app.tenant_id` matches no rows."""
    tenant_predicate = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    for table in TENANT_OWNED:
        op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {SCHEMA}.{table} "
            f"USING ({tenant_predicate}) WITH CHECK ({tenant_predicate})"
        )

    # `columns` has no tenant_id (Section 8.2); its tenancy is its table's, and the
    # subquery below is itself filtered by the `tables` policy.
    op.execute(f"ALTER TABLE {SCHEMA}.columns ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.columns FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY columns_tenant_isolation ON {SCHEMA}.columns "
        f"USING (EXISTS (SELECT 1 FROM {SCHEMA}.tables t WHERE t.id = table_id)) "
        f"WITH CHECK (EXISTS (SELECT 1 FROM {SCHEMA}.tables t WHERE t.id = table_id))"
    )


def _grant_app_role() -> None:
    """Grant the RLS-bound request-path role access. Guarded for databases without it."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                GRANT USAGE ON SCHEMA {SCHEMA} TO {APP_ROLE};
                GRANT SELECT, INSERT, UPDATE, DELETE
                    ON ALL TABLES IN SCHEMA {SCHEMA} TO {APP_ROLE};
                GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {SCHEMA} TO {APP_ROLE};
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_table("relationships", schema=SCHEMA)
    op.drop_table("columns", schema=SCHEMA)
    op.drop_table("tables", schema=SCHEMA)
    op.drop_table("schema_snapshots", schema=SCHEMA)
    op.drop_table("data_sources", schema=SCHEMA)
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.set_updated_at()")
