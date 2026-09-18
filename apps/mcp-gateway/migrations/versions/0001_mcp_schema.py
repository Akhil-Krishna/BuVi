"""mcp schema: servers, tools, tool_grants, invocations (Section 8.7)

Section 8.7 DDL plus Section 19: RLS on every table. `mcp.tools` has no tenant column in the
spec; it is isolated through its server (a policy over `mcp.servers`, itself tenant-filtered).
`mcp.invocations` is append-only for the request role: a security record is never rewritten.

Revision ID: 0001_mcp_schema
Revises:
Create Date: 2026-09-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_mcp_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "mcp"
APP_ROLE = "buvi_app"
_UUID = postgresql.UUID(as_uuid=True)
_NOW = sa.text("now()")
_GEN = sa.text("gen_random_uuid()")


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.create_table(
        "servers",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("endpoint_url", sa.Text(), nullable=False),
        sa.Column("auth_secret_ref", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.Text(), server_default=sa.text("'pending_approval'"), nullable=False
        ),
        sa.Column("approved_by", _UUID, nullable=True),
        sa.Column("created_by", _UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint(
            "status IN ('pending_approval','approved','disabled','rejected')",
            name="ck_servers_status",
        ),
        sa.PrimaryKeyConstraint("id", name="servers_pkey"),
        schema=SCHEMA,
    )
    op.create_index("idx_servers_tenant", "servers", ["tenant_id", "id"], schema=SCHEMA)
    op.create_table(
        "tools",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("server_id", _UUID, nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("tool_class", sa.Text(), nullable=False),
        sa.Column("default_policy", sa.Text(), server_default=sa.text("'deny'"), nullable=False),
        sa.CheckConstraint(
            "tool_class IN ('read_metadata','read_data','external_read','write','admin')",
            name="ck_tools_tool_class",
        ),
        sa.CheckConstraint(
            "default_policy IN ('allow','require_grant','deny')", name="ck_tools_default_policy"
        ),
        sa.ForeignKeyConstraint(
            ["server_id"], [f"{SCHEMA}.servers.id"], name="tools_server_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="tools_pkey"),
        sa.UniqueConstraint("server_id", "tool_name", name="tools_server_id_tool_name_key"),
        schema=SCHEMA,
    )
    op.create_table(
        "tool_grants",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("tool_id", _UUID, nullable=False),
        sa.Column("grantee_role", sa.Text(), nullable=True),
        sa.Column("grantee_user_id", _UUID, nullable=True),
        sa.Column("granted_by", _UUID, nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(
            ["tool_id"], [f"{SCHEMA}.tools.id"], name="tool_grants_tool_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="tool_grants_pkey"),
        schema=SCHEMA,
    )
    op.create_index("idx_tool_grants_tool", "tool_grants", ["tool_id"], schema=SCHEMA)
    op.create_table(
        "invocations",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("tool_id", _UUID, nullable=False),
        sa.Column("invoked_by", sa.Text(), nullable=False),
        sa.Column("run_id", _UUID, nullable=True),
        sa.Column("request_payload", postgresql.JSONB(), nullable=False),
        sa.Column("response_status", sa.Text(), nullable=False),
        sa.Column("response_summary", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint(
            "response_status IN ('ok','denied','error')", name="ck_invocations_response_status"
        ),
        sa.PrimaryKeyConstraint("id", name="invocations_pkey"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_invocations_tenant_created", "invocations", ["tenant_id", "created_at"], schema=SCHEMA
    )

    predicate = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    via_server = f"EXISTS (SELECT 1 FROM {SCHEMA}.servers s WHERE s.id = server_id)"
    for table, rule in (
        ("servers", predicate),
        ("tools", via_server),
        ("tool_grants", predicate),
        ("invocations", predicate),
    ):
        op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {SCHEMA}.{table} "
            f"USING ({rule}) WITH CHECK ({rule})"
        )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                GRANT USAGE ON SCHEMA {SCHEMA} TO {APP_ROLE};
                GRANT SELECT, INSERT, UPDATE ON {SCHEMA}.servers TO {APP_ROLE};
                GRANT SELECT, INSERT ON {SCHEMA}.tools TO {APP_ROLE};
                GRANT SELECT, INSERT, DELETE ON {SCHEMA}.tool_grants TO {APP_ROLE};
                GRANT SELECT, INSERT ON {SCHEMA}.invocations TO {APP_ROLE};
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_table("invocations", schema=SCHEMA)
    op.drop_table("tool_grants", schema=SCHEMA)
    op.drop_table("tools", schema=SCHEMA)
    op.drop_table("servers", schema=SCHEMA)
