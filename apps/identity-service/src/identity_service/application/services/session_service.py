"""Application sessions: creation, resolution, revocation (Sections 6.1, 6.9).

Section 6.1 describes this behaviour as the BFF's, and Phase A1 places the
`/auth/*` endpoints in identity-service. The reconciliation, recorded in ADR
0003: identity-service owns the OIDC exchange and the durable application
session (`identity.sessions`, Section 8.1, whose `idp_refresh_token_ref` column
only makes sense here), and Track B's Next.js BFF is a thin proxy in front of
it that holds the cookie and never sees a token.

The session cookie carries the session id and nothing else (Section 6.1 step 6).
IdP tokens go to Vault by reference and never to the browser (Section 6.2).
"""

from __future__ import annotations

import datetime as dt
import uuid

from identity_service.core.config import Settings
from identity_service.domain.errors import SessionExpiredError, UserNotActiveError
from identity_service.domain.policies.sessions import (
    SessionLifetime,
    absolute_expiry,
    is_expired,
)
from identity_service.infrastructure.db.models import Session, User
from identity_service.infrastructure.db.repositories.identity_repository import (
    IdentityRepository,
)
from identity_service.infrastructure.oidc.client import OidcTokens
from identity_service.infrastructure.secrets.store import SecretStore, session_token_ref
from platform_auth import Principal, permissions_for_roles


class SessionService:
    """Creates and resolves the application session behind the cookie."""

    def __init__(
        self,
        *,
        repository: IdentityRepository,
        secrets: SecretStore,
        settings: Settings,
    ) -> None:
        self._repository = repository
        self._secrets = secrets
        self._settings = settings
        self._lifetime = SessionLifetime.from_hours_and_days(
            settings.session_idle_timeout_hours,
            settings.session_absolute_lifetime_days,
        )

    @property
    def lifetime(self) -> SessionLifetime:
        return self._lifetime

    async def create(
        self,
        *,
        user: User,
        tokens: OidcTokens,
        device_label: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> Session:
        """Open a session and stash the IdP refresh token in Vault.

        The row is written first so the Vault path can name the session id, then
        the secret is written and the row updated with the reference. A crash
        between the two leaves an unusable session rather than an orphaned
        secret, which is the safer of the two failure modes.
        """
        now = dt.datetime.now(dt.UTC)
        session_id = uuid.uuid4()
        ref = session_token_ref(user.tenant_id, session_id)

        if tokens.refresh_token:
            await self._secrets.write(
                ref,
                {"refresh_token": tokens.refresh_token, "access_token": tokens.access_token},
            )

        row = Session(
            id=session_id,
            user_id=user.id,
            tenant_id=user.tenant_id,
            device_label=device_label,
            ip_address=ip_address,
            user_agent=user_agent,
            idp_refresh_token_ref=ref,
            created_at=now,
            last_seen_at=now,
            expires_at=absolute_expiry(now, self._lifetime),
        )
        return await self._repository.add_session(row)

    async def resolve(self, session_id: uuid.UUID) -> tuple[Session, User, Principal]:
        """Turn a session id into the caller's `Principal`.

        Permissions come from the Section 7.1 matrix applied to the roles stored
        in `identity.user_roles` -- never from a token claim, per Section 2's
        design rule. A session that is revoked, past its absolute lifetime, or
        idle beyond the timeout is refused here rather than at the endpoint.
        """
        row = await self._repository.get_session(session_id)
        if row is None:
            raise SessionExpiredError()

        now = dt.datetime.now(dt.UTC)
        if is_expired(
            now=now,
            last_seen_at=row.last_seen_at,
            expires_at=row.expires_at,
            revoked_at=row.revoked_at,
            lifetime=self._lifetime,
        ):
            raise SessionExpiredError()

        user = await self._repository.get_user(row.tenant_id, row.user_id)
        if user is None:
            raise SessionExpiredError()
        if user.status != "active":
            # A suspended or deactivated user's live sessions stop working
            # immediately, not at their next expiry (Section 6.7).
            raise UserNotActiveError()

        role_keys = await self._repository.get_user_role_keys(user.id)
        principal = Principal(
            user_id=str(user.id),
            tenant_id=str(user.tenant_id),
            permissions=permissions_for_roles(role_keys),
            auth_method="session",
            mfa_verified=row.mfa_verified_at is not None,
            session_id=str(row.id),
            mfa_verified_at=row.mfa_verified_at,
            roles=role_keys,
        )
        await self._repository.touch_session(row.id, now)
        return row, user, principal

    async def revoke(self, tenant_id: uuid.UUID, session_id: uuid.UUID) -> bool:
        """Revoke one session and destroy its stored IdP tokens."""
        revoked = await self._repository.revoke_session(
            tenant_id, session_id, dt.datetime.now(dt.UTC)
        )
        if revoked:
            await self._secrets.delete(session_token_ref(tenant_id, session_id))
        return bool(revoked)

    async def revoke_all_for_user(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> int:
        """Force-logout every session a user holds (Sections 6.7, 6.9)."""
        rows = await self._repository.list_sessions_for_user(tenant_id, user_id)
        count: int = await self._repository.revoke_all_sessions_for_user(
            tenant_id, user_id, dt.datetime.now(dt.UTC)
        )
        for row in rows:
            await self._secrets.delete(session_token_ref(tenant_id, row.id))
        return count

    async def read_refresh_token(self, session_row: Session) -> str | None:
        """Fetch a session's IdP refresh token, for logout at the IdP."""
        stored = await self._secrets.read(session_row.idp_refresh_token_ref)
        return stored.get("refresh_token") if stored else None

    async def mark_mfa_verified(self, session_id: uuid.UUID, at: dt.datetime) -> None:
        """Start the Section 7.3 step-up window for this session.

        Takes the instant the verification actually happened rather than
        generating a second one, so the audit row and the step-up window agree.
        """
        await self._repository.mark_session_mfa_verified(session_id, at)
