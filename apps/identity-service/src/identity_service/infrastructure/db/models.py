"""SQLAlchemy mapping of the `identity` schema (Section 8.1).

This is a transcription of the Section 8.1 DDL, not an interpretation of it.
Column names, types, nullability, check constraints, defaults, unique
constraints and indexes all match the spec; the Alembic migration in
`migrations/versions/` is generated against these models and carries the
Row-Level Security policies Section 19 requires on top.

Nothing here holds a secret in the clear: `sessions.idp_refresh_token_ref` is a
Vault path, `api_keys.secret_hash` is an argon2id hash, and
`invitations.token_hash` is a SHA-256 digest.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, INET, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "identity"

#: Section 8: `created_at`/`updated_at` default to `now()`; `updated_at` is
#: maintained by a trigger installed in the migration, not by the ORM, so a
#: direct SQL write cannot leave a stale timestamp behind.
_NOW = text("now()")
_GEN_UUID = text("gen_random_uuid()")


class Base(DeclarativeBase):
    """Declarative base scoped to the `identity` schema."""

    pass


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint("status IN ('active','suspended','deleted')", name="ck_tenants_status"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=_GEN_UUID
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    plan: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'trial'"))
    data_region: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'us'"))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "status IN ('invited','active','suspended','deactivated')", name="ck_users_status"
        ),
        UniqueConstraint("tenant_id", "email", name="users_tenant_id_email_key"),
        Index("idx_users_tenant", "tenant_id"),
        Index("idx_users_idp_subject", "idp_subject", unique=True),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=_GEN_UUID
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tenants.id"), nullable=False
    )
    #: Keycloak's `sub` claim. Globally unique: one IdP subject is one user.
    idp_subject: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'invited'"))
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (
        # Section 8.1 specifies UNIQUE (tenant_id, key) on a nullable tenant_id.
        # Plain SQL NULL semantics would let unlimited duplicate platform-level
        # roles through, so the constraint is declared NULLS NOT DISTINCT
        # (PostgreSQL 15+, and the spec pins PostgreSQL 16). See ADR 0002.
        UniqueConstraint(
            "tenant_id", "key", name="roles_tenant_id_key_key", postgresql_nulls_not_distinct=True
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=_GEN_UUID
    )
    #: NULL for platform-level roles (Section 8.1).
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tenants.id")
    )
    key: Mapped[str] = mapped_column(Text, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = ({"schema": SCHEMA},)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id")
    )
    granted_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class Invitation(Base):
    __tablename__ = "invitations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','accepted','revoked','expired')", name="ck_invitations_status"
        ),
        Index("idx_invitations_tenant", "tenant_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=_GEN_UUID
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tenants.id"), nullable=False
    )
    email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    role_key: Mapped[str] = mapped_column(Text, nullable=False)
    #: SHA-256 of the single-use token; the raw token exists only in the email.
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    invited_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        Index("idx_sessions_user", "user_id"),
        Index("idx_sessions_token_hash", "token_hash", unique=True),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=_GEN_UUID
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tenants.id"), nullable=False
    )
    device_label: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    #: SHA-256 of the opaque cookie token (Section 8.1). The raw token is returned
    #: once, to set the cookie, and is never stored or logged; the row id is not a
    #: credential.
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    #: Vault path, never the raw token (Section 8.1, Section 24).
    idp_refresh_token_ref: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    #: Section 7.3 step-up freshness. Not part of the Section 8.1 DDL; added so
    #: "MFA verified in the last 5 minutes" survives a process restart instead of
    #: living in process memory. See ADR 0002.
    mfa_verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        Index("idx_api_keys_tenant", "tenant_id"),
        Index("idx_api_keys_prefix", "key_prefix"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=_GEN_UUID
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tenants.id"), nullable=False
    )
    #: NULL for pure service accounts (Section 8.1).
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    key_prefix: Mapped[str] = mapped_column(Text, nullable=False)
    #: argon2id hash of the full key (Section 6.8). Never reversible.
    secret_hash: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id"), nullable=False
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class AuditEvent(Base):
    """Append-only (Section 8.1).

    UPDATE and DELETE are revoked from the application role in the migration, so
    the append-only property is enforced by the database and not only by the
    absence of code that would rewrite history.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('user','service_account','system')", name="ck_audit_events_actor_type"
        ),
        Index("idx_audit_tenant_time", "tenant_id", text("created_at DESC")),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=_GEN_UUID
    )
    #: NULL for platform-level events (Section 8.1).
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'user'"))
    #: Set during `platform_super_admin` impersonation (Sections 2, 8.1).
    acting_as_tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str | None] = mapped_column(Text)
    resource_id: Mapped[str | None] = mapped_column(Text)
    before_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    request_id: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class MfaCredential(Base):
    """An MFA factor (Sections 6.6, 8.1).

    Secret material lives in Vault (`secret_ref`). A row is an active factor only
    once `confirmed_at` is set and while `revoked_at` is NULL.
    """

    __tablename__ = "mfa_credentials"
    __table_args__ = (
        CheckConstraint("method IN ('totp','webauthn')", name="ck_mfa_credentials_method"),
        Index("idx_mfa_credentials_user", "user_id"),
        Index(
            "idx_mfa_credentials_one_totp",
            "user_id",
            unique=True,
            postgresql_where=text("method = 'totp' AND revoked_at IS NULL"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=_GEN_UUID
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tenants.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    method: Mapped[str] = mapped_column(Text, nullable=False)
    #: Vault path; the secret itself is never stored in Postgres (Section 24).
    secret_ref: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(Text)
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    #: Last accepted use; for TOTP also the replay guard (Section 8.1).
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


#: Tables carrying `tenant_id`, for the RLS policies in the migration (Section 19).
TENANT_OWNED_TABLES: tuple[str, ...] = (
    "users",
    "invitations",
    "sessions",
    "api_keys",
    "audit_events",
    "mfa_credentials",
)

__all__ = [
    "SCHEMA",
    "TENANT_OWNED_TABLES",
    "ApiKey",
    "AuditEvent",
    "Base",
    "Invitation",
    "MfaCredential",
    "Role",
    "Session",
    "Tenant",
    "User",
    "UserRole",
]
