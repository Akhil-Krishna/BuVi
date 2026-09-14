"""The OIDC login and logout flow (Section 6.1).

Ordering matters and is deliberate:

  login    -> mint PKCE, park the verifier in a short-lived HttpOnly cookie,
              redirect to the IdP
  callback -> verify state, exchange the code server-side, verify the ID token,
              resolve or JIT-provision the user, open a session, audit
  logout    -> revoke locally first, then tell the IdP; a failure at the IdP
              must never leave the local session alive
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from identity_service.application.services import audit_service as events
from identity_service.application.services.audit_service import AuditService
from identity_service.application.services.session_service import SessionService
from identity_service.core.config import Settings
from identity_service.domain.errors import (
    OidcStateMismatchError,
    UserNotActiveError,
)
from identity_service.domain.policies.roles import default_role_for_jit_provisioning
from identity_service.domain.value_objects.pkce import PkceChallenge, create_pkce_challenge
from identity_service.infrastructure.db.models import Session, User
from identity_service.infrastructure.db.repositories.identity_repository import (
    IdentityRepository,
)
from identity_service.infrastructure.oidc.client import OidcClient, OidcIdentity


@dataclass(frozen=True)
class LoginRedirect:
    """Where to send the browser, and the PKCE state to park in a cookie."""

    authorization_url: str
    challenge: PkceChallenge


@dataclass(frozen=True)
class LoginResult:
    session: Session
    user: User
    is_new_user: bool


class AuthService:
    def __init__(
        self,
        *,
        repository: IdentityRepository,
        oidc: OidcClient,
        sessions: SessionService,
        audit: AuditService,
        settings: Settings,
    ) -> None:
        self._repository = repository
        self._oidc = oidc
        self._sessions = sessions
        self._audit = audit
        self._settings = settings

    def begin_login(self) -> LoginRedirect:
        """Step 2 of Section 6.1: build the authorization request."""
        challenge = create_pkce_challenge()
        url = self._oidc.authorization_url(
            challenge=challenge.challenge,
            state=challenge.state,
            nonce=challenge.nonce,
        )
        return LoginRedirect(authorization_url=url, challenge=challenge)

    async def complete_login(
        self,
        *,
        code: str,
        returned_state: str,
        expected_state: str,
        verifier: str,
        nonce: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> LoginResult:
        """Steps 4-6 of Section 6.1.

        `state` is compared before the code is spent: an attacker-supplied
        callback should cost nothing at the IdP.
        """
        if not returned_state or returned_state != expected_state:
            raise OidcStateMismatchError()

        tokens = await self._oidc.exchange_code(code=code, verifier=verifier)
        identity = await self._oidc.verify_id_token(tokens.id_token, nonce=nonce)

        user, is_new_user = await self._resolve_user(identity)
        if user.status not in ("active", "invited"):
            await self._audit.record(
                event_type=events.EVENT_LOGIN_FAILED,
                tenant_id=user.tenant_id,
                actor_user_id=user.id,
                resource_type="user",
                resource_id=str(user.id),
                after_state={"reason": "user_not_active", "status": user.status},
                ip_address=ip_address,
            )
            raise UserNotActiveError()

        if user.status == "invited":
            # Section 6.5: the invitation email is the verification step, and
            # completing it through the IdP is what activates the account.
            await self._repository.set_user_status(user.tenant_id, user.id, "active")
            user.status = "active"

        session = await self._sessions.create(
            user=user,
            tokens=tokens,
            device_label=None,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        await self._repository.record_login(user.tenant_id, user.id, dt.datetime.now(dt.UTC))
        await self._audit.record(
            event_type=events.EVENT_LOGIN,
            tenant_id=user.tenant_id,
            actor_user_id=user.id,
            resource_type="session",
            resource_id=str(session.id),
            after_state={"auth_method": "session", "is_new_user": is_new_user},
            ip_address=ip_address,
        )
        return LoginResult(session=session, user=user, is_new_user=is_new_user)

    async def _resolve_user(self, identity: OidcIdentity) -> tuple[User, bool]:
        """Find the user behind a verified ID token, provisioning if needed.

        Section 6.4: a first federated login creates the record with the default
        `client` role unless SCIM already provisioned it.
        """
        existing = await self._repository.find_user_by_idp_subject(identity.subject)
        if existing is not None:
            # A subject bound to a user must still present that user's email.
            if identity.email and identity.email.lower() != existing.email.lower():
                raise UserNotActiveError("This account is not provisioned for this identity.")
            await self._repository.bind_tenant(existing.tenant_id)
            return existing, False

        tenant = None
        if identity.tenant_slug:
            tenant = await self._repository.find_tenant_by_slug(identity.tenant_slug)
        if tenant is None:
            # No tenant claim, or an unknown one: the token is valid but this
            # platform has nowhere to put the user. Refuse rather than guess.
            raise UserNotActiveError("This account is not provisioned for any organization.")
        await self._repository.bind_tenant(tenant.id)

        user = User(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            idp_subject=identity.subject,
            email=identity.email,
            display_name=identity.display_name or identity.email,
            status="active",
        )
        await self._repository.add_user(user)
        role = await self._repository.ensure_role(tenant.id, default_role_for_jit_provisioning())
        await self._repository.grant_role(user_id=user.id, role_id=role.id, granted_by=None)
        await self._audit.record(
            event_type=events.EVENT_USER_PROVISIONED,
            tenant_id=tenant.id,
            actor_type="system",
            resource_type="user",
            resource_id=str(user.id),
            after_state={
                "email": user.email,
                "roles": [default_role_for_jit_provisioning()],
                "source": "jit",
            },
        )
        return user, True

    async def logout(self, *, session_row: Session, ip_address: str | None) -> None:
        """Revoke locally, then best-effort at the IdP (Section 9)."""
        refresh_token = await self._sessions.read_refresh_token(session_row)
        await self._sessions.revoke(session_row.tenant_id, session_row.id)
        await self._audit.record(
            event_type=events.EVENT_LOGOUT,
            tenant_id=session_row.tenant_id,
            actor_user_id=session_row.user_id,
            resource_type="session",
            resource_id=str(session_row.id),
            ip_address=ip_address,
        )
        if refresh_token:
            await self._oidc.end_session(refresh_token)
