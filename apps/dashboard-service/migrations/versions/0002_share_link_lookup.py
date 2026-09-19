"""share_links: token lookup before a tenant is known (Section 8.6; Phase A10)

`GET /share/{token}` is public, so the tenant is what the token *establishes*: the lookup
cannot be tenant-scoped. As in identity-service (ADR 0002), a FOR SELECT policy is enabled only
while `app.share_lookup` is `on`, set for one transaction by the lookup and keyed on the
token's hash. It cannot write (a SELECT policy has no WITH CHECK). Everything after the lookup
runs bound to the tenant the link belongs to.

Revision ID: 0002_share_link_lookup
Revises: 0001_dashboard_schema
Create Date: 2026-09-19
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_share_link_lookup"
down_revision: str | None = "0001_dashboard_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "dashboard"


def upgrade() -> None:
    op.create_index(
        "idx_share_links_token_hash", "share_links", ["token_hash"], unique=True, schema=SCHEMA
    )
    op.create_index("idx_share_links_dashboard", "share_links", ["dashboard_id"], schema=SCHEMA)
    op.execute(
        f"CREATE POLICY share_links_token_lookup ON {SCHEMA}.share_links "
        f"FOR SELECT USING (current_setting('app.share_lookup', true) = 'on')"
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY share_links_token_lookup ON {SCHEMA}.share_links")
    op.drop_index("idx_share_links_dashboard", "share_links", schema=SCHEMA)
    op.drop_index("idx_share_links_token_hash", "share_links", schema=SCHEMA)
