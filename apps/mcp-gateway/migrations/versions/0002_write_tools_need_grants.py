"""mcp.tools: write/admin tools need a grant, not a blanket deny (Phase A10)

Phase A9 stored `deny` for write/admin tools because step-up confirmation did not exist yet.
It does now, so every class is `require_grant`; the service adds a fresh step-up for
write/admin (grant and each invocation) and `org_admin` for admin tools. No existing grant is
widened: write/admin tools could not be granted before this revision. The column default stays
`deny` (Section 8.7): the service always sets the policy explicitly.

Revision ID: 0002_write_tools_need_grants
Revises: 0001_mcp_schema
Create Date: 2026-09-19
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_write_tools_need_grants"
down_revision: str | None = "0001_mcp_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "mcp"


def _as_owner(statement: str) -> None:
    """RLS is FORCEd on `mcp.tools`; a data fix runs as the owner, across tenants."""
    op.execute(f"ALTER TABLE {SCHEMA}.tools NO FORCE ROW LEVEL SECURITY")
    op.execute(statement)
    op.execute(f"ALTER TABLE {SCHEMA}.tools FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    _as_owner(
        f"UPDATE {SCHEMA}.tools SET default_policy = 'require_grant' "
        f"WHERE tool_class IN ('write','admin') AND default_policy = 'deny'"
    )


def downgrade() -> None:
    _as_owner(
        f"UPDATE {SCHEMA}.tools SET default_policy = 'deny' "
        f"WHERE tool_class IN ('write','admin')"
    )
