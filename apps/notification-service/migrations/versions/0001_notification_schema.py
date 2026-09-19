"""notification schema: notifications, webhook_subscriptions (Section 8.8)

Section 8.8 DDL (with the Phase A11 additions: `notifications.event_key` and its uniqueness, which
make a redelivered event a no-op, and `webhook_subscriptions.created_by`) plus Section 19: RLS on
both tables. The request-path role may mark a notification read and disable a subscription, and
never delete either.

Revision ID: 0001_notification_schema
Revises:
Create Date: 2026-09-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_notification_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"
APP_ROLE = "buvi_app"
_UUID = postgresql.UUID(as_uuid=True)
_NOW = sa.text("now()")
_GEN = sa.text("gen_random_uuid()")


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.create_table(
        "notifications",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("user_id", _UUID, nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("template_key", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("event_key", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint(
            "channel IN ('in_app','email','webhook')", name="ck_notifications_channel"
        ),
        sa.CheckConstraint(
            "status IN ('queued','sent','failed','read')", name="ck_notifications_status"
        ),
        sa.PrimaryKeyConstraint("id", name="notifications_pkey"),
        sa.UniqueConstraint(
            "event_key", "user_id", "channel", name="notifications_event_key_user_id_channel_key"
        ),
        schema=SCHEMA,
    )
    op.execute(
        f"CREATE INDEX idx_notifications_inbox ON {SCHEMA}.notifications "
        "(tenant_id, user_id, created_at DESC) WHERE channel = 'in_app'"
    )
    op.create_table(
        "webhook_subscriptions",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("event_types", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("signing_secret_ref", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("created_by", _UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint(
            "status IN ('active','disabled')", name="ck_webhook_subscriptions_status"
        ),
        sa.PrimaryKeyConstraint("id", name="webhook_subscriptions_pkey"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_webhook_subscriptions_tenant",
        "webhook_subscriptions",
        ["tenant_id", "status"],
        schema=SCHEMA,
    )
    predicate = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    for table in ("notifications", "webhook_subscriptions"):
        op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {SCHEMA}.{table} "
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                GRANT USAGE ON SCHEMA {SCHEMA} TO {APP_ROLE};
                GRANT SELECT, INSERT, UPDATE ON {SCHEMA}.notifications TO {APP_ROLE};
                GRANT SELECT, INSERT, UPDATE ON {SCHEMA}.webhook_subscriptions TO {APP_ROLE};
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_table("webhook_subscriptions", schema=SCHEMA)
    op.drop_table("notifications", schema=SCHEMA)
