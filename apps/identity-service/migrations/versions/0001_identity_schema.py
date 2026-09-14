"""identity schema: tenants, users, roles, invitations, sessions, api keys, audit

Implements the Section 8.1 DDL, plus the cross-cutting rules Section 8's
preamble and Section 19 attach to it:

* `updated_at` maintained by trigger, not by application code;
* Row-Level Security enabled on every tenant-owned table, as defense in depth
  behind the application-layer tenant checks;
* `audit_events` append-only -- UPDATE and DELETE revoked from the app role.

Revision ID: 0001_identity_schema
Revises:
Create Date: 2026-09-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_identity_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "identity"
APP_ROLE = "buvi_app"

#: Tables carrying `tenant_id` directly (Section 19).
TENANT_OWNED = (
    "users",
    "invitations",
    "sessions",
    "api_keys",
    "audit_events",
    "mfa_credentials",
)


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    # `CREATE EXTENSION` needs superuser. The compose stack installs both
    # extensions cluster-wide in `infra/compose/postgres-init/`, and an
    # ephemeral test database runs migrations as superuser -- so only create
    # them where they are genuinely absent, rather than failing on a
    # privilege check for a no-op.
    _create_extension_if_absent("citext")
    _create_extension_if_absent("pgcrypto")

    # --- tenants ------------------------------------------------------------
    op.create_table(
        "tenants",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("plan", sa.Text(), server_default=sa.text("'trial'"), nullable=False),
        sa.Column("data_region", sa.Text(), server_default=sa.text("'us'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active','suspended','deleted')", name="ck_tenants_status"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
        schema=SCHEMA,
    )

    # --- users --------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idp_subject", sa.Text(), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'invited'"), nullable=False),
        sa.Column("mfa_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('invited','active','suspended','deactivated')", name="ck_users_status"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], [f"{SCHEMA}.tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "email", name="users_tenant_id_email_key"),
        schema=SCHEMA,
    )
    op.create_index("idx_users_tenant", "users", ["tenant_id"], schema=SCHEMA)
    op.create_index("idx_users_idp_subject", "users", ["idp_subject"], unique=True, schema=SCHEMA)

    # --- roles --------------------------------------------------------------
    # Section 8.1 declares UNIQUE (tenant_id, key) with a nullable tenant_id.
    # Under default SQL semantics NULLs are distinct, which would allow
    # unlimited duplicate platform-level roles; NULLS NOT DISTINCT (PG15+, and
    # the spec pins PG16) makes the constraint mean what Section 8.1 intends.
    op.create_table(
        "roles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], [f"{SCHEMA}.tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.roles ADD CONSTRAINT roles_tenant_id_key_key "
        f"UNIQUE NULLS NOT DISTINCT (tenant_id, key)"
    )

    # --- user_roles ---------------------------------------------------------
    op.create_table(
        "user_roles",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("granted_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], [f"{SCHEMA}.users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], [f"{SCHEMA}.roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["granted_by"], [f"{SCHEMA}.users.id"]),
        sa.PrimaryKeyConstraint("user_id", "role_id"),
        schema=SCHEMA,
    )

    # --- invitations --------------------------------------------------------
    op.create_table(
        "invitations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("role_key", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("invited_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending','accepted','revoked','expired')", name="ck_invitations_status"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], [f"{SCHEMA}.tenants.id"]),
        sa.ForeignKeyConstraint(["invited_by"], [f"{SCHEMA}.users.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index("idx_invitations_tenant", "invitations", ["tenant_id"], schema=SCHEMA)
    # Acceptance looks an invitation up by token hash alone, before a tenant is
    # known; this index keeps that a single-row probe rather than a scan.
    op.create_index("idx_invitations_token_hash", "invitations", ["token_hash"], schema=SCHEMA)

    # --- sessions -----------------------------------------------------------
    op.create_table(
        "sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_label", sa.Text(), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("idp_refresh_token_ref", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        # Extends Section 8.1: persists Section 7.3 step-up freshness so it
        # survives a process restart. See ADR 0002.
        sa.Column("mfa_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], [f"{SCHEMA}.users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], [f"{SCHEMA}.tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index("idx_sessions_user", "sessions", ["user_id"], schema=SCHEMA)

    # --- api_keys -----------------------------------------------------------
    op.create_table(
        "api_keys",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("key_prefix", sa.Text(), nullable=False),
        sa.Column("secret_hash", sa.Text(), nullable=False),
        sa.Column(
            "scopes",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], [f"{SCHEMA}.tenants.id"]),
        sa.ForeignKeyConstraint(["owner_user_id"], [f"{SCHEMA}.users.id"]),
        sa.ForeignKeyConstraint(["created_by"], [f"{SCHEMA}.users.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index("idx_api_keys_tenant", "api_keys", ["tenant_id"], schema=SCHEMA)
    op.create_index("idx_api_keys_prefix", "api_keys", ["key_prefix"], schema=SCHEMA)

    # --- audit_events -------------------------------------------------------
    op.create_table(
        "audit_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_type", sa.Text(), server_default=sa.text("'user'"), nullable=False),
        sa.Column("acting_as_tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=True),
        sa.Column("resource_id", sa.Text(), nullable=True),
        sa.Column("before_state", postgresql.JSONB(), nullable=True),
        sa.Column("after_state", postgresql.JSONB(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "actor_type IN ('user','service_account','system')",
            name="ck_audit_events_actor_type",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.execute(
        f"CREATE INDEX idx_audit_tenant_time ON {SCHEMA}.audit_events (tenant_id, created_at DESC)"
    )

    # --- mfa_credentials ----------------------------------------------------
    # Not in the Section 8.1 DDL. TOTP verification (Section 6.6, required by
    # Phase A1) needs somewhere to record the enrolment and a reference to the
    # shared secret; `users.mfa_enabled` alone cannot carry it. See ADR 0002.
    op.create_table(
        "mfa_credentials",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("method", sa.Text(), server_default=sa.text("'totp'"), nullable=False),
        sa.Column("secret_ref", sa.Text(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_step", sa.BigInteger(), nullable=True),
        sa.Column("failed_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("method IN ('totp')", name="ck_mfa_credentials_method"),
        sa.ForeignKeyConstraint(["tenant_id"], [f"{SCHEMA}.tenants.id"]),
        sa.ForeignKeyConstraint(["user_id"], [f"{SCHEMA}.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "method", name="mfa_credentials_user_id_method_key"),
        schema=SCHEMA,
    )

    _install_updated_at_trigger()
    _enable_row_level_security()
    _make_audit_append_only()
    _grant_app_role()


def _create_extension_if_absent(name: str) -> None:
    """Install an extension only when the cluster does not already have it."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = '{name}') THEN
                CREATE EXTENSION {name};
            END IF;
        END
        $$;
        """
    )


def _install_updated_at_trigger() -> None:
    """Section 8: `updated_at` is maintained by trigger, not by the ORM."""
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.set_updated_at() RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in ("tenants", "users", "mfa_credentials"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON {SCHEMA}.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.set_updated_at()"
        )


def _enable_row_level_security() -> None:
    """Section 19: RLS on every tenant-owned table, as defense in depth.

    Policies read `app.tenant_id`, which the application sets at the start of
    each request-scoped session. `current_setting(..., true)` returns NULL when
    the GUC is unset, and `tenant_id = NULL` is never true -- so an unscoped
    session sees no rows. Deny by default.
    """
    tenant_predicate = (
        "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    )

    for table in TENANT_OWNED:
        op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
        # FORCE applies the policy to the table owner too, so a future migration
        # that runs DML cannot quietly sidestep it.
        op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {SCHEMA}.{table} "
            f"USING ({tenant_predicate}) WITH CHECK ({tenant_predicate})"
        )

    # Four lookups must run before a tenant is known, because resolving the
    # tenant is exactly what they do: a session by its cookie id, a user by the
    # `sub` of a verified ID token, an invitation by its emailed token, and an
    # API key by its non-secret prefix (Section 6.8). Under
    # the policy above they would match zero rows and login could never
    # succeed. Each gets a second, SELECT-only policy gated on a distinct GUC
    # that `pre_auth_scope()` sets for the duration of that one query.
    #
    # This is a read exception and cannot become a write exception: a policy
    # declared FOR SELECT carries no WITH CHECK clause, so no INSERT or UPDATE
    # can travel through it. Each lookup is also keyed on a value that is
    # already a credential -- a session id, an IdP-signed subject, a 256-bit
    # token hash -- or, for API keys, a prefix whose candidate rows still have
    # to survive an argon2 verification. None of it is enumerable by an
    # unauthenticated caller. See ADR 0002.
    for table in ("users", "sessions", "invitations", "api_keys"):
        op.execute(
            f"CREATE POLICY {table}_pre_auth_lookup ON {SCHEMA}.{table} "
            f"FOR SELECT USING (current_setting('app.pre_auth_lookup', true) = 'on')"
        )

    # `tenants` is the tenant: its own id is the scoping column.
    op.execute(f"ALTER TABLE {SCHEMA}.tenants ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.tenants FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenants_tenant_isolation ON {SCHEMA}.tenants "
        f"USING (id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        f"WITH CHECK (id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )

    # `roles` holds tenant roles and platform roles (tenant_id NULL). Platform
    # role *definitions* are not tenant data, so they stay readable.
    op.execute(f"ALTER TABLE {SCHEMA}.roles ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.roles FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY roles_tenant_isolation ON {SCHEMA}.roles "
        f"USING (tenant_id IS NULL OR {tenant_predicate}) "
        f"WITH CHECK (tenant_id IS NULL OR {tenant_predicate})"
    )

    # `user_roles` carries no tenant_id; its tenancy is the user's.
    op.execute(f"ALTER TABLE {SCHEMA}.user_roles ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.user_roles FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY user_roles_tenant_isolation ON {SCHEMA}.user_roles "
        f"USING (EXISTS (SELECT 1 FROM {SCHEMA}.users u WHERE u.id = user_id)) "
        f"WITH CHECK (EXISTS (SELECT 1 FROM {SCHEMA}.users u WHERE u.id = user_id))"
    )


def _make_audit_append_only() -> None:
    """Section 8.1: revoke UPDATE/DELETE on the audit log for the app role."""
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON {SCHEMA}.audit_events FROM PUBLIC")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                REVOKE UPDATE, DELETE, TRUNCATE ON {SCHEMA}.audit_events FROM {APP_ROLE};
            END IF;
        END
        $$;
        """
    )


def _grant_app_role() -> None:
    """Grant the RLS-bound request-path role access to this schema's tables.

    Guarded so the migration also runs against an ephemeral test database that
    has no `buvi_app` role (testcontainers, Section 25).
    """
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                GRANT USAGE ON SCHEMA {SCHEMA} TO {APP_ROLE};
                GRANT SELECT, INSERT, UPDATE, DELETE
                    ON ALL TABLES IN SCHEMA {SCHEMA} TO {APP_ROLE};
                GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {SCHEMA} TO {APP_ROLE};
                REVOKE UPDATE, DELETE, TRUNCATE ON {SCHEMA}.audit_events FROM {APP_ROLE};
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    for table in ("mfa_credentials", "audit_events", "api_keys", "sessions", "invitations"):
        op.drop_table(table, schema=SCHEMA)
    op.drop_table("user_roles", schema=SCHEMA)
    op.drop_table("roles", schema=SCHEMA)
    op.drop_table("users", schema=SCHEMA)
    op.drop_table("tenants", schema=SCHEMA)
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.set_updated_at()")
