"""query_gateway schema: query_executions (Section 8.5)

Implements the Section 8.5 DDL, plus Section 19 and the audit rules:

* Row-Level Security on `query_executions`, as defense in depth;
* append-only: UPDATE, DELETE and TRUNCATE revoked from the request-path role, so a
  compromised or buggy request path cannot rewrite the query audit (Sections 13, 22).

Revision ID: 0001_query_gateway_schema
Revises:
Create Date: 2026-09-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_query_gateway_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "query_gateway"
APP_ROLE = "buvi_app"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.create_table(
        "query_executions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("data_source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("sql_text", sa.Text(), nullable=False),
        sa.Column("sql_hash", sa.Text(), nullable=False),
        sa.Column("validation_result", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("bytes_returned", sa.BigInteger(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("result_handle", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('validated','rejected','running','succeeded','failed','timeout')",
            name="ck_query_executions_status",
        ),
        sa.PrimaryKeyConstraint("id", name="query_executions_pkey"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_qe_tenant_time",
        "query_executions",
        ["tenant_id", sa.text("created_at DESC")],
        schema=SCHEMA,
    )

    predicate = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    op.execute(f"ALTER TABLE {SCHEMA}.query_executions ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.query_executions FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY query_executions_tenant_isolation ON {SCHEMA}.query_executions "
        f"USING ({predicate}) WITH CHECK ({predicate})"
    )

    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON {SCHEMA}.query_executions FROM PUBLIC")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                GRANT USAGE ON SCHEMA {SCHEMA} TO {APP_ROLE};
                GRANT SELECT, INSERT ON {SCHEMA}.query_executions TO {APP_ROLE};
                REVOKE UPDATE, DELETE, TRUNCATE ON {SCHEMA}.query_executions FROM {APP_ROLE};
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_table("query_executions", schema=SCHEMA)
