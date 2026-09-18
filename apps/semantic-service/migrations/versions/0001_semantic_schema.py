"""semantic schema: metrics, dimensions, join_rules (Section 8.3)

Section 8.3 DDL (with created_by/approved_at and non-null references, spec commit 1a6283f) plus
Section 19: RLS on every table. The request-path role cannot delete definitions; a metric leaves
use by being deprecated, which keeps artifacts that reference it explainable.

Revision ID: 0001_semantic_schema
Revises:
Create Date: 2026-09-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_semantic_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "semantic"
APP_ROLE = "buvi_app"
_UUID = postgresql.UUID(as_uuid=True)
_NOW = sa.text("now()")
_GEN = sa.text("gen_random_uuid()")
_TEXT_ARRAY = postgresql.ARRAY(sa.Text())


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.create_table(
        "metrics",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("expression", sa.Text(), nullable=False),
        sa.Column("default_grain", sa.Text(), nullable=True),
        sa.Column("base_table_id", _UUID, nullable=False),
        sa.Column("synonyms", _TEXT_ARRAY, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("created_by", _UUID, nullable=False),
        sa.Column("approved_by", _UUID, nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'draft'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint("status IN ('draft','approved','deprecated')", name="ck_metrics_status"),
        sa.PrimaryKeyConstraint("id", name="metrics_pkey"),
        sa.UniqueConstraint("tenant_id", "name", name="metrics_tenant_id_name_key"),
        schema=SCHEMA,
    )
    op.create_index("idx_metrics_tenant_status", "metrics", ["tenant_id", "status"], schema=SCHEMA)
    op.create_table(
        "dimensions",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("column_id", _UUID, nullable=False),
        sa.Column("synonyms", _TEXT_ARRAY, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("created_by", _UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="dimensions_pkey"),
        sa.UniqueConstraint("tenant_id", "name", name="dimensions_tenant_id_name_key"),
        schema=SCHEMA,
    )
    op.create_table(
        "join_rules",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("left_table_id", _UUID, nullable=False),
        sa.Column("right_table_id", _UUID, nullable=False),
        sa.Column("on_expression", sa.Text(), nullable=False),
        sa.Column("is_approved", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="join_rules_pkey"),
        schema=SCHEMA,
    )

    predicate = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    for table in ("metrics", "dimensions", "join_rules"):
        op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_tenant_isolation ON {SCHEMA}.{table} USING ({predicate}) WITH CHECK ({predicate})")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                GRANT USAGE ON SCHEMA {SCHEMA} TO {APP_ROLE};
                GRANT SELECT, INSERT, UPDATE ON {SCHEMA}.metrics TO {APP_ROLE};
                GRANT SELECT, INSERT ON {SCHEMA}.dimensions TO {APP_ROLE};
                GRANT SELECT ON {SCHEMA}.join_rules TO {APP_ROLE};
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_table("join_rules", schema=SCHEMA)
    op.drop_table("dimensions", schema=SCHEMA)
    op.drop_table("metrics", schema=SCHEMA)
