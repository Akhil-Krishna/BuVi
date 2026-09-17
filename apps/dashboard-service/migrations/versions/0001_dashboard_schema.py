"""dashboard schema: artifacts, dashboards, tiles, share_links (Section 8.6)

Section 8.6 DDL (with `artifacts.summary`, spec commit e54692e) plus Section 19: RLS on every
table. Artifacts are immutable for the request-path role -- a change is a new version (Section 16).

Revision ID: 0001_dashboard_schema
Revises:
Create Date: 2026-09-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_dashboard_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "dashboard"
APP_ROLE = "buvi_app"
_UUID = postgresql.UUID(as_uuid=True)
_NOW = sa.text("now()")
_GEN = sa.text("gen_random_uuid()")


def _created_at() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False)


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.create_table(
        "dashboards",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("owner_id", _UUID, nullable=False),
        sa.Column("visibility", sa.Text(), server_default=sa.text("'private'"), nullable=False),
        _created_at(),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint("visibility IN ('private','tenant','link')", name="ck_dashboards_visibility"),
        sa.PrimaryKeyConstraint("id", name="dashboards_pkey"),
        schema=SCHEMA,
    )
    op.create_index("idx_dashboards_tenant_owner", "dashboards", ["tenant_id", "owner_id"], schema=SCHEMA)
    op.create_table(
        "tiles",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("dashboard_id", _UUID, nullable=False),
        sa.Column("artifact_id", _UUID, nullable=False),
        sa.Column("chart_spec_version", sa.Integer(), nullable=False),
        sa.Column("position", postgresql.JSONB(), nullable=False),
        sa.Column("overrides", postgresql.JSONB(), server_default=sa.text("'{}'"), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["dashboard_id"], [f"{SCHEMA}.dashboards.id"], name="tiles_dashboard_id_fkey", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="tiles_pkey"),
        schema=SCHEMA,
    )
    op.create_index("idx_tiles_tenant_dashboard", "tiles", ["tenant_id", "dashboard_id"], schema=SCHEMA)
    op.create_table(
        "share_links",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("dashboard_id", _UUID, nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("created_by", _UUID, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.ForeignKeyConstraint(["dashboard_id"], [f"{SCHEMA}.dashboards.id"], name="share_links_dashboard_id_fkey", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="share_links_pkey"),
        schema=SCHEMA,
    )
    op.create_table(
        "artifacts",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("conversation_id", _UUID, nullable=True),
        sa.Column("run_id", _UUID, nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("semantic_query", postgresql.JSONB(), nullable=False),
        sa.Column("source_refs", postgresql.JSONB(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("validated_sql", sa.Text(), nullable=False),
        sa.Column("query_result_ref", sa.Text(), nullable=False),
        sa.Column("result_schema", postgresql.JSONB(), nullable=False),
        sa.Column("chart_spec", postgresql.JSONB(), nullable=False),
        sa.Column("refresh_policy", postgresql.JSONB(), server_default=sa.text("""'{"mode":"manual"}'"""), nullable=False),
        sa.Column("created_by", _UUID, nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="artifacts_pkey"),
        schema=SCHEMA,
    )
    op.create_index("idx_artifacts_tenant_run", "artifacts", ["tenant_id", "run_id"], schema=SCHEMA)

    predicate = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    for table in ("dashboards", "tiles", "share_links", "artifacts"):
        op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_tenant_isolation ON {SCHEMA}.{table} USING ({predicate}) WITH CHECK ({predicate})")
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON {SCHEMA}.artifacts FROM PUBLIC")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                GRANT USAGE ON SCHEMA {SCHEMA} TO {APP_ROLE};
                GRANT SELECT, INSERT, UPDATE, DELETE ON {SCHEMA}.dashboards, {SCHEMA}.tiles TO {APP_ROLE};
                GRANT SELECT, INSERT, UPDATE ON {SCHEMA}.share_links TO {APP_ROLE};
                GRANT SELECT, INSERT ON {SCHEMA}.artifacts TO {APP_ROLE};
                REVOKE UPDATE, DELETE, TRUNCATE ON {SCHEMA}.artifacts FROM {APP_ROLE};
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_table("artifacts", schema=SCHEMA)
    op.drop_table("share_links", schema=SCHEMA)
    op.drop_table("tiles", schema=SCHEMA)
    op.drop_table("dashboards", schema=SCHEMA)
