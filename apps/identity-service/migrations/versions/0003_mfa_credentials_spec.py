"""mfa_credentials: align with the canonical Section 8.1 DDL

The canonical spec now defines `identity.mfa_credentials`: `label`,
`last_used_at` and `revoked_at` columns, `webauthn` as an allowed method, and at
most one active TOTP factor per user. `last_used_step`, `failed_attempts` and
`updated_at` are not part of it and are dropped; the TOTP replay guard carries
over as `last_used_at`.

Revision ID: 0003_mfa_credentials_spec
Revises: 0002_session_token_hash
Create Date: 2026-09-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_mfa_credentials_spec"
down_revision: str | None = "0002_session_token_hash"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "identity"
TABLE = f"{SCHEMA}.mfa_credentials"
ONE_TOTP = "method = 'totp' AND revoked_at IS NULL"


def _without_forced_rls(*statements: str) -> None:
    """Run data statements as the table owner; RLS is FORCEd on this table.

    One statement per `op.execute`: asyncpg prepares each call and refuses
    several commands in a single prepared statement.
    """
    op.execute(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")
    for statement in statements:
        op.execute(statement)
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.execute(f"ALTER TABLE {TABLE} DROP CONSTRAINT mfa_credentials_user_id_method_key")
    op.execute(f"ALTER TABLE {TABLE} DROP CONSTRAINT ck_mfa_credentials_method")
    op.execute(
        f"ALTER TABLE {TABLE} ADD CONSTRAINT ck_mfa_credentials_method "
        f"CHECK (method IN ('totp','webauthn'))"
    )
    op.execute(f"ALTER TABLE {TABLE} ALTER COLUMN method DROP DEFAULT")

    op.add_column("mfa_credentials", sa.Column("label", sa.Text()), schema=SCHEMA)
    op.add_column(
        "mfa_credentials", sa.Column("last_used_at", sa.DateTime(timezone=True)), schema=SCHEMA
    )
    op.add_column(
        "mfa_credentials", sa.Column("revoked_at", sa.DateTime(timezone=True)), schema=SCHEMA
    )
    # TOTP step n begins at n * 30 seconds, so the replay guard survives exactly.
    _without_forced_rls(
        f"UPDATE {TABLE} SET last_used_at = to_timestamp(last_used_step * 30) "
        f"WHERE last_used_step IS NOT NULL"
    )

    op.execute(f"DROP TRIGGER IF EXISTS trg_mfa_credentials_updated_at ON {TABLE}")
    for column in ("last_used_step", "failed_attempts", "updated_at"):
        op.drop_column("mfa_credentials", column, schema=SCHEMA)

    op.create_index("idx_mfa_credentials_user", "mfa_credentials", ["user_id"], schema=SCHEMA)
    op.create_index(
        "idx_mfa_credentials_one_totp",
        "mfa_credentials",
        ["user_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text(ONE_TOTP),
    )


def downgrade() -> None:
    op.drop_index("idx_mfa_credentials_one_totp", table_name="mfa_credentials", schema=SCHEMA)
    op.drop_index("idx_mfa_credentials_user", table_name="mfa_credentials", schema=SCHEMA)

    op.add_column(
        "mfa_credentials", sa.Column("last_used_step", sa.BigInteger()), schema=SCHEMA
    )
    op.add_column(
        "mfa_credentials",
        sa.Column("failed_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        schema=SCHEMA,
    )
    op.add_column(
        "mfa_credentials",
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    # The old schema cannot represent WebAuthn or revoked factors.
    _without_forced_rls(
        f"DELETE FROM {TABLE} WHERE method <> 'totp' OR revoked_at IS NOT NULL",
        f"UPDATE {TABLE} SET last_used_step = floor(extract(epoch FROM last_used_at) / 30) "
        f"WHERE last_used_at IS NOT NULL",
    )
    op.execute(
        f"CREATE TRIGGER trg_mfa_credentials_updated_at BEFORE UPDATE ON {TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.set_updated_at()"
    )
    for column in ("label", "last_used_at", "revoked_at"):
        op.drop_column("mfa_credentials", column, schema=SCHEMA)
    op.execute(f"ALTER TABLE {TABLE} ALTER COLUMN method SET DEFAULT 'totp'")
    op.execute(f"ALTER TABLE {TABLE} DROP CONSTRAINT ck_mfa_credentials_method")
    op.execute(
        f"ALTER TABLE {TABLE} ADD CONSTRAINT ck_mfa_credentials_method CHECK (method IN ('totp'))"
    )
    op.execute(
        f"ALTER TABLE {TABLE} ADD CONSTRAINT mfa_credentials_user_id_method_key "
        f"UNIQUE (user_id, method)"
    )
