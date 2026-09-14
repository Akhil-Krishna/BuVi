"""sessions.token_hash: sessions are looked up by a hashed opaque token

Implements the Section 8.1 `identity.sessions.token_hash` column. The session
cookie now carries a random opaque token; only its SHA-256 is stored, so a
database read no longer yields a usable session credential.

Revision ID: 0002_session_token_hash
Revises: 0001_identity_schema
Create Date: 2026-09-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_session_token_hash"
down_revision: str | None = "0001_identity_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "identity"


def upgrade() -> None:
    op.add_column("sessions", sa.Column("token_hash", sa.Text(), nullable=True), schema=SCHEMA)

    # Existing sessions were identified by their raw row id, which is no longer a
    # credential, and no holder knows a token for them. They are revoked and given
    # an unguessable placeholder hash. RLS is FORCEd on this table, so the owner
    # lifts FORCE for the backfill; otherwise the UPDATE would match zero rows and
    # SET NOT NULL would fail. All of it runs in the migration's transaction.
    op.execute(f"ALTER TABLE {SCHEMA}.sessions NO FORCE ROW LEVEL SECURITY")
    op.execute(
        f"UPDATE {SCHEMA}.sessions "
        f"SET token_hash = encode(gen_random_bytes(32), 'hex'), "
        f"revoked_at = COALESCE(revoked_at, now()) "
        f"WHERE token_hash IS NULL"
    )
    op.execute(f"ALTER TABLE {SCHEMA}.sessions FORCE ROW LEVEL SECURITY")

    op.alter_column("sessions", "token_hash", nullable=False, schema=SCHEMA)
    op.create_index(
        "idx_sessions_token_hash", "sessions", ["token_hash"], unique=True, schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_index("idx_sessions_token_hash", table_name="sessions", schema=SCHEMA)
    op.drop_column("sessions", "token_hash", schema=SCHEMA)
