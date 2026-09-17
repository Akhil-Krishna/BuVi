"""analytics schema: conversations, messages, runs, run_events (Section 8.4)

Section 8.4 DDL plus Section 19: RLS on every table (`run_events` has no `tenant_id` in the DDL and
is scoped through its run), and `run_events` is append-only for the request-path role -- it is the
user-visible execution trace.

Revision ID: 0001_analytics_schema
Revises:
Create Date: 2026-09-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_analytics_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "analytics"
APP_ROLE = "buvi_app"
_UUID = postgresql.UUID(as_uuid=True)
_NOW = sa.text("now()")
_GEN = sa.text("gen_random_uuid()")


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.create_table(
        "conversations",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("created_by", _UUID, nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="conversations_pkey"),
        schema=SCHEMA,
    )
    op.create_index("idx_conversations_tenant", "conversations", ["tenant_id", "created_at"], schema=SCHEMA)
    op.create_table(
        "messages",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("conversation_id", _UUID, nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("run_id", _UUID, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint("role IN ('user','assistant','system')", name="ck_messages_role"),
        sa.ForeignKeyConstraint(["conversation_id"], [f"{SCHEMA}.conversations.id"], name="messages_conversation_id_fkey", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="messages_pkey"),
        schema=SCHEMA,
    )
    op.create_index("idx_messages_tenant_conversation", "messages", ["tenant_id", "conversation_id", "created_at"], schema=SCHEMA)
    op.create_table(
        "runs",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("conversation_id", _UUID, nullable=False),
        sa.Column("requested_by", _UUID, nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("current_stage", sa.Text(), nullable=True),
        sa.Column("flow_state", postgresql.JSONB(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint(
            "status IN ('queued','running','waiting_query','completed','failed','cancelled')",
            name="ck_runs_status",
        ),
        sa.ForeignKeyConstraint(["conversation_id"], [f"{SCHEMA}.conversations.id"], name="runs_conversation_id_fkey"),
        sa.PrimaryKeyConstraint("id", name="runs_pkey"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="runs_tenant_id_idempotency_key_key"),
        schema=SCHEMA,
    )
    op.create_index("idx_runs_tenant_status", "runs", ["tenant_id", "status"], schema=SCHEMA)
    op.create_table(
        "run_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("run_id", _UUID, nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("artifact_id", _UUID, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint("status IN ('started','completed','failed')", name="ck_run_events_status"),
        sa.ForeignKeyConstraint(["run_id"], [f"{SCHEMA}.runs.id"], name="run_events_run_id_fkey", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="run_events_pkey"),
        sa.UniqueConstraint("run_id", "seq", name="run_events_run_id_seq_key"),
        schema=SCHEMA,
    )

    predicate = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    for table in ("conversations", "messages", "runs"):
        op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_tenant_isolation ON {SCHEMA}.{table} USING ({predicate}) WITH CHECK ({predicate})")
    op.execute(f"ALTER TABLE {SCHEMA}.run_events ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.run_events FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY run_events_tenant_isolation ON {SCHEMA}.run_events "
        f"USING (EXISTS (SELECT 1 FROM {SCHEMA}.runs r WHERE r.id = run_id)) "
        f"WITH CHECK (EXISTS (SELECT 1 FROM {SCHEMA}.runs r WHERE r.id = run_id))"
    )
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON {SCHEMA}.run_events FROM PUBLIC")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                GRANT USAGE ON SCHEMA {SCHEMA} TO {APP_ROLE};
                GRANT SELECT, INSERT, UPDATE, DELETE ON {SCHEMA}.conversations, {SCHEMA}.messages, {SCHEMA}.runs TO {APP_ROLE};
                GRANT SELECT, INSERT ON {SCHEMA}.run_events TO {APP_ROLE};
                GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {SCHEMA} TO {APP_ROLE};
                REVOKE UPDATE, DELETE, TRUNCATE ON {SCHEMA}.run_events FROM {APP_ROLE};
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_table("run_events", schema=SCHEMA)
    op.drop_table("runs", schema=SCHEMA)
    op.drop_table("messages", schema=SCHEMA)
    op.drop_table("conversations", schema=SCHEMA)
