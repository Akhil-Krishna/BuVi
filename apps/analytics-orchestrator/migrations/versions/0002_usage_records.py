"""analytics.usage_records: one row per billing.usage.recorded event (Section 8.4; Phase A11)

worker-runtime consumes the events and writes them through `POST /internal/v1/billing/usage-records`.
The producer's `event_id` is the primary key, so a redelivered event is stored once. Append-only
for the request-path role; RLS by tenant like every analytics table.

Revision ID: 0002_usage_records
Revises: 0001_analytics_schema
Create Date: 2026-09-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_usage_records"
down_revision: str | None = "0001_analytics_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "analytics"
APP_ROLE = "buvi_app"
_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "usage_records",
        sa.Column("event_id", _UUID, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("stage", sa.Text(), nullable=True),
        sa.Column("run_id", _UUID, nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity >= 0", name="ck_usage_records_quantity"),
        sa.PrimaryKeyConstraint("event_id", name="usage_records_pkey"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_usage_records_tenant_time",
        "usage_records",
        ["tenant_id", "occurred_at"],
        schema=SCHEMA,
    )
    predicate = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    op.execute(f"ALTER TABLE {SCHEMA}.usage_records ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.usage_records FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY usage_records_tenant_isolation ON {SCHEMA}.usage_records "
        f"USING ({predicate}) WITH CHECK ({predicate})"
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                GRANT SELECT, INSERT ON {SCHEMA}.usage_records TO {APP_ROLE};
                REVOKE UPDATE, DELETE, TRUNCATE ON {SCHEMA}.usage_records FROM {APP_ROLE};
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_table("usage_records", schema=SCHEMA)
