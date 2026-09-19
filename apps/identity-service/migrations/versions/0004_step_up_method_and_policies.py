"""Phase A10: step-up by method, WebAuthn challenges, tenant policies (Sections 6.6, 7.3, 8.1)

* `sessions.mfa_verified_method`: how the session's last MFA check was made. A step-up that
  must be WebAuthn (Section 6.6) is judged on it.
* `sessions.webauthn_challenge` / `webauthn_challenge_expires_at`: the one pending WebAuthn
  challenge of a session. Bound to the session, cleared on use, short-lived.
* `identity.tenant_policies`: one row per tenant (Section 3, "policy mapping"). A missing row
  means every default, and every default is the most restrictive setting.

Revision ID: 0004_step_up_method_and_policies
Revises: 0003_mfa_credentials_spec
Create Date: 2026-09-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_step_up_method_and_policies"
down_revision: str | None = "0003_mfa_credentials_spec"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "identity"
APP_ROLE = "buvi_app"
_UUID = postgresql.UUID(as_uuid=True)
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.add_column("sessions", sa.Column("mfa_verified_method", sa.Text()), schema=SCHEMA)
    op.create_check_constraint(
        "ck_sessions_mfa_verified_method",
        "sessions",
        "mfa_verified_method IN ('totp','webauthn')",
        schema=SCHEMA,
    )
    op.add_column("sessions", sa.Column("webauthn_challenge", sa.Text()), schema=SCHEMA)
    op.add_column(
        "sessions",
        sa.Column("webauthn_challenge_expires_at", sa.DateTime(timezone=True)),
        schema=SCHEMA,
    )
    # Every step-up recorded before this revision was a TOTP check (the only factor A1 had).
    op.execute(f"ALTER TABLE {SCHEMA}.sessions NO FORCE ROW LEVEL SECURITY")
    op.execute(
        f"UPDATE {SCHEMA}.sessions SET mfa_verified_method = 'totp' "
        f"WHERE mfa_verified_at IS NOT NULL"
    )
    op.execute(f"ALTER TABLE {SCHEMA}.sessions FORCE ROW LEVEL SECURITY")

    op.create_table(
        "tenant_policies",
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column(
            "client_can_share_dashboards", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column(
            "developer_can_manage_mcp", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column(
            "org_admin_requires_webauthn", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("updated_by", _UUID, nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], [f"{SCHEMA}.tenants.id"], name="tenant_policies_tenant_id_fkey"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], [f"{SCHEMA}.users.id"], name="tenant_policies_updated_by_fkey"
        ),
        sa.PrimaryKeyConstraint("tenant_id", name="tenant_policies_pkey"),
        schema=SCHEMA,
    )
    op.execute(f"ALTER TABLE {SCHEMA}.tenant_policies ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.tenant_policies FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_policies_tenant_isolation ON {SCHEMA}.tenant_policies "
        f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                GRANT SELECT, INSERT, UPDATE ON {SCHEMA}.tenant_policies TO {APP_ROLE};
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_table("tenant_policies", schema=SCHEMA)
    op.drop_column("sessions", "webauthn_challenge_expires_at", schema=SCHEMA)
    op.drop_column("sessions", "webauthn_challenge", schema=SCHEMA)
    op.drop_constraint("ck_sessions_mfa_verified_method", "sessions", schema=SCHEMA)
    op.drop_column("sessions", "mfa_verified_method", schema=SCHEMA)
