"""metadata.data_source_grants: per-connection `sql:execute` grants (Section 7.1; Phase A10)

Section 7.1 gives `developer` `sql:execute` "(per-connection grant)". A row lets one user run
SQL-editor queries against one data source; `org_admin` needs none. Tenant-owned: RLS like
every other table here.

Revision ID: 0002_data_source_grants
Revises: 0001_metadata_schema
Create Date: 2026-09-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_data_source_grants"
down_revision: str | None = "0001_metadata_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "metadata"
APP_ROLE = "buvi_app"
_UUID = postgresql.UUID(as_uuid=True)
PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "data_source_grants",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("data_source_id", _UUID, nullable=False),
        sa.Column("user_id", _UUID, nullable=False),
        sa.Column("granted_by", _UUID, nullable=False),
        sa.Column(
            "granted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["data_source_id"],
            [f"{SCHEMA}.data_sources.id"],
            name="data_source_grants_data_source_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="data_source_grants_pkey"),
        sa.UniqueConstraint(
            "data_source_id", "user_id", name="data_source_grants_data_source_id_user_id_key"
        ),
        schema=SCHEMA,
    )
    op.execute(f"ALTER TABLE {SCHEMA}.data_source_grants ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.data_source_grants FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY data_source_grants_tenant_isolation ON {SCHEMA}.data_source_grants "
        f"USING ({PREDICATE}) WITH CHECK ({PREDICATE})"
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                GRANT SELECT, INSERT, DELETE ON {SCHEMA}.data_source_grants TO {APP_ROLE};
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_table("data_source_grants", schema=SCHEMA)
