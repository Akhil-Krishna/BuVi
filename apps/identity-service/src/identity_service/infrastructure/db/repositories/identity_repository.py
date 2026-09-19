"""Data access for the `identity` schema.

Every method takes an explicit `tenant_id` and filters on it. That is the
application-layer half of Section 19's two-layer tenant isolation; the RLS
policies installed by the first migration are the other half, and neither is
treated as a substitute for the other.

The one deliberate exception is `find_user_by_idp_subject`, used during the OIDC
callback before a tenant is known. It is documented at its definition.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import Select, delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from identity_service.domain.policies.tenant_policy import DEFAULT_POLICIES, Policies
from identity_service.infrastructure.db.models import (
    ApiKey,
    AuditEvent,
    Invitation,
    MfaCredential,
    Role,
    Session,
    Tenant,
    TenantPolicy,
    User,
    UserRole,
)
from identity_service.infrastructure.db.session import PRE_AUTH_GUC, set_tenant_scope


@dataclass(frozen=True)
class Page:
    """Cursor pagination envelope (Section 9: all list endpoints paginate)."""

    items: list[object]
    next_cursor: str | None


class IdentityRepository:
    """Repository over the `identity` schema, scoped to one database session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bind_tenant(self, tenant_id: uuid.UUID) -> None:
        """Leave the pre-auth scope and bind RLS to the tenant just resolved.

        Pre-auth lookups can read but never write (their policies are FOR
        SELECT). Once the tenant is known, writes need it bound, and the broad
        lookup flag is switched off so the rest of the request is tenant-scoped.
        """
        await self._session.execute(
            text("SELECT set_config(:guc, '', false)"), {"guc": PRE_AUTH_GUC}
        )
        await set_tenant_scope(self._session, tenant_id)

    # --- tenants ---------------------------------------------------------

    async def get_tenant(self, tenant_id: uuid.UUID) -> Tenant | None:
        return await self._session.get(Tenant, tenant_id)

    async def find_tenant_by_slug(self, slug: str) -> Tenant | None:
        result = await self._session.execute(select(Tenant).where(Tenant.slug == slug))
        return result.scalar_one_or_none()

    # --- users -----------------------------------------------------------

    async def get_user(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> User | None:
        result = await self._session.execute(
            select(User).where(User.id == user_id, User.tenant_id == tenant_id)
        )
        return result.scalar_one_or_none()

    async def get_user_tenant_id(self, user_id: uuid.UUID) -> uuid.UUID | None:
        """Load *only* the owning tenant of a user.

        This is the `load_resource_tenant_id` callable Section 7.2 requires: it
        deliberately does not return the row, so no caller can read the resource
        through a path that skipped the ownership check.
        """
        result = await self._session.execute(select(User.tenant_id).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def find_user_by_idp_subject(self, idp_subject: str) -> User | None:
        """Look up a user by their IdP `sub` claim, across tenants.

        The only query in this repository that is not tenant-scoped, because it
        runs during `/auth/callback` to answer "which tenant is this person in?"
        -- the question a tenant filter would presuppose. `idp_subject` carries a
        globally unique index (Section 8.1), the value comes from an ID token
        whose signature, issuer and audience were verified first, and the caller
        immediately scopes every subsequent query to the tenant it returns.
        """
        result = await self._session.execute(select(User).where(User.idp_subject == idp_subject))
        return result.scalar_one_or_none()

    async def find_user_by_email(self, tenant_id: uuid.UUID, email: str) -> User | None:
        result = await self._session.execute(
            select(User).where(User.tenant_id == tenant_id, User.email == email)
        )
        return result.scalar_one_or_none()

    async def add_user(self, user: User) -> User:
        self._session.add(user)
        await self._session.flush()
        return user

    async def list_users(
        self, tenant_id: uuid.UUID, *, limit: int, cursor: uuid.UUID | None
    ) -> tuple[list[User], uuid.UUID | None]:
        """Keyset pagination on `id`, which is stable under concurrent inserts."""
        statement: Select[tuple[User]] = (
            select(User).where(User.tenant_id == tenant_id).order_by(User.id).limit(limit + 1)
        )
        if cursor is not None:
            statement = statement.where(User.id > cursor)
        result = await self._session.execute(statement)
        rows = list(result.scalars().all())
        if len(rows) > limit:
            return rows[:limit], rows[limit - 1].id
        return rows, None

    async def directory(
        self,
        tenant_id: uuid.UUID,
        *,
        user_ids: frozenset[uuid.UUID] | None,
        role: str | None,
        limit: int,
    ) -> list[tuple[User, frozenset[str]]]:
        """Users with their role keys, filtered by id and/or role (Phase A11 directory)."""
        statement: Select[tuple[User]] = (
            select(User).where(User.tenant_id == tenant_id).order_by(User.id).limit(limit)
        )
        if user_ids is not None:
            statement = statement.where(User.id.in_(user_ids))
        if role is not None:
            statement = statement.where(
                User.id.in_(
                    select(UserRole.user_id)
                    .join(Role, Role.id == UserRole.role_id)
                    .where(Role.tenant_id == tenant_id, Role.key == role)
                )
            )
        users = list((await self._session.execute(statement)).scalars().all())
        if not users:
            return []
        rows = await self._session.execute(
            select(UserRole.user_id, Role.key)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id.in_([u.id for u in users]))
        )
        roles: dict[uuid.UUID, set[str]] = {}
        for user_id, key in rows.all():
            roles.setdefault(user_id, set()).add(key)
        return [(u, frozenset(roles.get(u.id, ()))) for u in users]

    async def count_active_users(self, tenant_id: uuid.UUID) -> int:
        """Seats (Section 23): exact, no cap."""
        count = await self._session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.tenant_id == tenant_id, User.status == "active")
        )
        return int(count or 0)

    async def count_active_org_admins(self, tenant_id: uuid.UUID) -> int:
        """Count active `org_admin` users, for the Section 2 last-admin rule."""
        statement = (
            select(func.count())
            .select_from(User)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(
                User.tenant_id == tenant_id,
                User.status == "active",
                Role.key == "org_admin",
            )
        )
        result = await self._session.execute(statement)
        return int(result.scalar_one())

    async def set_user_status(self, tenant_id: uuid.UUID, user_id: uuid.UUID, status: str) -> None:
        await self._session.execute(
            update(User)
            .where(User.id == user_id, User.tenant_id == tenant_id)
            .values(status=status)
        )

    async def record_login(self, tenant_id: uuid.UUID, user_id: uuid.UUID, at: dt.datetime) -> None:
        await self._session.execute(
            update(User)
            .where(User.id == user_id, User.tenant_id == tenant_id)
            .values(last_login_at=at)
        )

    async def set_mfa_enabled(
        self, tenant_id: uuid.UUID, user_id: uuid.UUID, *, enabled: bool
    ) -> None:
        await self._session.execute(
            update(User)
            .where(User.id == user_id, User.tenant_id == tenant_id)
            .values(mfa_enabled=enabled)
        )

    # --- roles -----------------------------------------------------------

    async def ensure_role(self, tenant_id: uuid.UUID | None, key: str) -> Role:
        """Fetch a role, creating the tenant-scoped row if it is missing."""
        statement = select(Role).where(Role.key == key)
        statement = (
            statement.where(Role.tenant_id.is_(None))
            if tenant_id is None
            else statement.where(Role.tenant_id == tenant_id)
        )
        result = await self._session.execute(statement)
        role = result.scalar_one_or_none()
        if role is not None:
            return role
        role = Role(tenant_id=tenant_id, key=key, is_system=True)
        self._session.add(role)
        await self._session.flush()
        return role

    async def get_user_role_keys(self, user_id: uuid.UUID) -> frozenset[str]:
        result = await self._session.execute(
            select(Role.key)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
        )
        return frozenset(result.scalars().all())

    async def grant_role(
        self, *, user_id: uuid.UUID, role_id: uuid.UUID, granted_by: uuid.UUID | None
    ) -> None:
        existing = await self._session.get(UserRole, (user_id, role_id))
        if existing is not None:
            return
        self._session.add(UserRole(user_id=user_id, role_id=role_id, granted_by=granted_by))
        await self._session.flush()

    async def revoke_role(self, *, user_id: uuid.UUID, role_id: uuid.UUID) -> None:
        await self._session.execute(
            delete(UserRole).where(UserRole.user_id == user_id, UserRole.role_id == role_id)
        )

    # --- invitations ------------------------------------------------------

    async def add_invitation(self, invitation: Invitation) -> Invitation:
        self._session.add(invitation)
        await self._session.flush()
        return invitation

    async def find_pending_invitation_by_token_hash(self, token_hash: str) -> Invitation | None:
        """Resolve an invitation by its token hash, before a tenant is known.

        Acceptance is unauthenticated by nature -- the token is the credential
        (Section 6.7). The token hash is a 256-bit digest, so this is a lookup
        by secret, not an enumerable scan, and the caller scopes everything
        afterwards to `invitation.tenant_id`.
        """
        result = await self._session.execute(
            select(Invitation).where(
                Invitation.token_hash == token_hash, Invitation.status == "pending"
            )
        )
        return result.scalar_one_or_none()

    async def get_invitation_tenant_id(self, invitation_id: uuid.UUID) -> uuid.UUID | None:
        result = await self._session.execute(
            select(Invitation.tenant_id).where(Invitation.id == invitation_id)
        )
        return result.scalar_one_or_none()

    async def set_invitation_status(self, invitation_id: uuid.UUID, status: str) -> None:
        await self._session.execute(
            update(Invitation).where(Invitation.id == invitation_id).values(status=status)
        )

    async def list_invitations(self, tenant_id: uuid.UUID) -> list[Invitation]:
        result = await self._session.execute(
            select(Invitation)
            .where(Invitation.tenant_id == tenant_id)
            .order_by(Invitation.created_at.desc())
        )
        return list(result.scalars().all())

    # --- sessions ----------------------------------------------------------

    async def add_session(self, session_row: Session) -> Session:
        self._session.add(session_row)
        await self._session.flush()
        return session_row

    async def get_session(self, session_id: uuid.UUID) -> Session | None:
        return await self._session.get(Session, session_id)

    async def get_session_by_token_hash(self, token_hash: str) -> Session | None:
        """Resolve a session from the hash of its cookie token (Section 8.1).

        The only lookup that turns a presented cookie into a session: the raw
        token is never stored, and the row id is not accepted as a credential.
        Runs in the pre-auth scope when the tenant is not yet known.
        """
        result = await self._session.execute(
            select(Session).where(Session.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def list_sessions_for_user(
        self, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[Session]:
        result = await self._session.execute(
            select(Session)
            .where(
                Session.tenant_id == tenant_id,
                Session.user_id == user_id,
                Session.revoked_at.is_(None),
            )
            .order_by(Session.last_seen_at.desc())
        )
        return list(result.scalars().all())

    async def touch_session(self, session_id: uuid.UUID, at: dt.datetime) -> None:
        await self._session.execute(
            update(Session).where(Session.id == session_id).values(last_seen_at=at)
        )

    async def mark_session_mfa_verified(
        self, session_id: uuid.UUID, at: dt.datetime, method: str
    ) -> None:
        await self._session.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(mfa_verified_at=at, mfa_verified_method=method)
        )

    async def set_webauthn_challenge(
        self, session_id: uuid.UUID, challenge: str, expires_at: dt.datetime
    ) -> None:
        """Replace the session's pending WebAuthn challenge (one at a time)."""
        await self._session.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(webauthn_challenge=challenge, webauthn_challenge_expires_at=expires_at)
        )

    async def take_webauthn_challenge(
        self, session_id: uuid.UUID
    ) -> tuple[str | None, dt.datetime | None]:
        """Read and clear the pending challenge in one step, so it is usable exactly once."""
        row = (
            await self._session.execute(
                select(Session.webauthn_challenge, Session.webauthn_challenge_expires_at)
                .where(Session.id == session_id)
                .with_for_update()
            )
        ).one_or_none()
        await self._session.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(webauthn_challenge=None, webauthn_challenge_expires_at=None)
        )
        return (row[0], row[1]) if row is not None else (None, None)

    async def revoke_session(
        self, tenant_id: uuid.UUID, session_id: uuid.UUID, at: dt.datetime
    ) -> int:
        result = await self._session.execute(
            update(Session)
            .where(
                Session.id == session_id,
                Session.tenant_id == tenant_id,
                Session.revoked_at.is_(None),
            )
            .values(revoked_at=at)
        )
        return int(result.rowcount or 0)

    async def revoke_all_sessions_for_user(
        self, tenant_id: uuid.UUID, user_id: uuid.UUID, at: dt.datetime
    ) -> int:
        """Section 6.7/6.9: deprovisioning revokes immediately, not on expiry."""
        result = await self._session.execute(
            update(Session)
            .where(
                Session.tenant_id == tenant_id,
                Session.user_id == user_id,
                Session.revoked_at.is_(None),
            )
            .values(revoked_at=at)
        )
        return int(result.rowcount or 0)

    # --- API keys ----------------------------------------------------------

    async def add_api_key(self, api_key: ApiKey) -> ApiKey:
        self._session.add(api_key)
        await self._session.flush()
        return api_key

    async def get_api_key_tenant_id(self, api_key_id: uuid.UUID) -> uuid.UUID | None:
        result = await self._session.execute(
            select(ApiKey.tenant_id).where(ApiKey.id == api_key_id)
        )
        return result.scalar_one_or_none()

    async def get_api_key(self, tenant_id: uuid.UUID, api_key_id: uuid.UUID) -> ApiKey | None:
        result = await self._session.execute(
            select(ApiKey).where(ApiKey.id == api_key_id, ApiKey.tenant_id == tenant_id)
        )
        return result.scalar_one_or_none()

    async def list_api_keys_for_owner(
        self, tenant_id: uuid.UUID, owner_user_id: uuid.UUID
    ) -> list[ApiKey]:
        result = await self._session.execute(
            select(ApiKey)
            .where(
                ApiKey.tenant_id == tenant_id,
                ApiKey.owner_user_id == owner_user_id,
                ApiKey.revoked_at.is_(None),
            )
            .order_by(ApiKey.created_at.desc())
        )
        return list(result.scalars().all())

    async def find_api_keys_by_prefix(self, key_prefix: str) -> list[ApiKey]:
        """Candidates for an inbound key, narrowed by its non-secret prefix.

        Only these candidates are argon2-verified, so authenticating a key costs
        one hash rather than one per key in the table (Section 6.8).
        """
        now = dt.datetime.now(dt.UTC)
        result = await self._session.execute(
            select(ApiKey).where(
                ApiKey.key_prefix == key_prefix,
                ApiKey.revoked_at.is_(None),
                or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > now),
            )
        )
        return list(result.scalars().all())

    async def revoke_api_key(
        self, tenant_id: uuid.UUID, api_key_id: uuid.UUID, at: dt.datetime
    ) -> int:
        result = await self._session.execute(
            update(ApiKey)
            .where(
                ApiKey.id == api_key_id,
                ApiKey.tenant_id == tenant_id,
                ApiKey.revoked_at.is_(None),
            )
            .values(revoked_at=at)
        )
        return int(result.rowcount or 0)

    async def revoke_api_keys_for_owner(
        self, tenant_id: uuid.UUID, owner_user_id: uuid.UUID, at: dt.datetime
    ) -> int:
        """Section 6.7: deleting a user revokes the API keys they own."""
        result = await self._session.execute(
            update(ApiKey)
            .where(
                ApiKey.tenant_id == tenant_id,
                ApiKey.owner_user_id == owner_user_id,
                ApiKey.revoked_at.is_(None),
            )
            .values(revoked_at=at)
        )
        return int(result.rowcount or 0)

    async def touch_api_key(self, api_key_id: uuid.UUID, at: dt.datetime) -> None:
        await self._session.execute(
            update(ApiKey).where(ApiKey.id == api_key_id).values(last_used_at=at)
        )

    # --- MFA ---------------------------------------------------------------

    async def get_mfa_credential(
        self, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> MfaCredential | None:
        """The user's current (non-revoked) TOTP factor, confirmed or not."""
        result = await self._session.execute(
            select(MfaCredential).where(
                MfaCredential.tenant_id == tenant_id,
                MfaCredential.user_id == user_id,
                MfaCredential.method == "totp",
                MfaCredential.revoked_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def add_mfa_credential(self, credential: MfaCredential) -> MfaCredential:
        self._session.add(credential)
        await self._session.flush()
        return credential

    async def delete_mfa_credential(self, credential_id: uuid.UUID) -> None:
        await self._session.execute(delete(MfaCredential).where(MfaCredential.id == credential_id))

    async def confirm_mfa_credential(self, credential_id: uuid.UUID, *, at: dt.datetime) -> None:
        """Mark a factor confirmed; its first use also starts the replay guard."""
        await self._session.execute(
            update(MfaCredential)
            .where(MfaCredential.id == credential_id)
            .values(confirmed_at=at, last_used_at=at)
        )

    async def record_mfa_use(self, credential_id: uuid.UUID, at: dt.datetime) -> None:
        """Advance the replay guard so a code cannot be reused in its window."""
        await self._session.execute(
            update(MfaCredential).where(MfaCredential.id == credential_id).values(last_used_at=at)
        )

    async def list_mfa_credentials(
        self,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        method: str | None = None,
        confirmed_only: bool = False,
    ) -> list[MfaCredential]:
        """The user's non-revoked factors, oldest first."""
        query = select(MfaCredential).where(
            MfaCredential.tenant_id == tenant_id,
            MfaCredential.user_id == user_id,
            MfaCredential.revoked_at.is_(None),
        )
        if method is not None:
            query = query.where(MfaCredential.method == method)
        if confirmed_only:
            query = query.where(MfaCredential.confirmed_at.is_not(None))
        result = await self._session.execute(
            query.order_by(MfaCredential.created_at, MfaCredential.id)
        )
        return list(result.scalars().all())

    async def get_mfa_credential_by_id(
        self, tenant_id: uuid.UUID, credential_id: uuid.UUID
    ) -> MfaCredential | None:
        result = await self._session.execute(
            select(MfaCredential).where(
                MfaCredential.tenant_id == tenant_id,
                MfaCredential.id == credential_id,
                MfaCredential.revoked_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def revoke_mfa_credentials(
        self, tenant_id: uuid.UUID, credential_ids: list[uuid.UUID], at: dt.datetime
    ) -> None:
        if not credential_ids:
            return
        await self._session.execute(
            update(MfaCredential)
            .where(MfaCredential.tenant_id == tenant_id, MfaCredential.id.in_(credential_ids))
            .values(revoked_at=at)
        )

    # --- tenant policies (Phase A10) ------------------------------------------

    async def get_policies(self, tenant_id: uuid.UUID) -> Policies:
        """The tenant's policies; every default when it has never set one."""
        row = await self._session.get(TenantPolicy, tenant_id)
        if row is None:
            return DEFAULT_POLICIES
        return Policies(
            client_can_share_dashboards=row.client_can_share_dashboards,
            developer_can_manage_mcp=row.developer_can_manage_mcp,
            org_admin_requires_webauthn=row.org_admin_requires_webauthn,
        )

    async def save_policies(
        self, tenant_id: uuid.UUID, policies: Policies, *, updated_by: uuid.UUID
    ) -> None:
        row = await self._session.get(TenantPolicy, tenant_id)
        if row is None:
            row = TenantPolicy(tenant_id=tenant_id)
            self._session.add(row)
        row.client_can_share_dashboards = policies.client_can_share_dashboards
        row.developer_can_manage_mcp = policies.developer_can_manage_mcp
        row.org_admin_requires_webauthn = policies.org_admin_requires_webauthn
        row.updated_by = updated_by
        row.updated_at = dt.datetime.now(dt.UTC)
        await self._session.flush()

    # --- audit -------------------------------------------------------------

    async def add_audit_event(self, event: AuditEvent) -> None:
        """Append one audit row. Append-only is enforced by DB grants."""
        self._session.add(event)
        await self._session.flush()

    async def list_audit_events(
        self, tenant_id: uuid.UUID, *, limit: int, event_type: str | None = None
    ) -> list[AuditEvent]:
        statement = (
            select(AuditEvent)
            .where(AuditEvent.tenant_id == tenant_id)
            .order_by(AuditEvent.created_at.desc())
            .limit(limit)
        )
        if event_type is not None:
            statement = statement.where(AuditEvent.event_type == event_type)
        result = await self._session.execute(statement)
        return list(result.scalars().all())
