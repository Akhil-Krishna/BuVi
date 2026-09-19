"""User lifecycle: invitations, roles, deletion (Sections 2, 6.7).

The rules that make this more than CRUD:

* an invitation is a single-use hashed token with a 7-day expiry, and the raw
  token exists only in the recipient's inbox (Section 6.7);
* a tenant can never lose its last `org_admin` (Section 2), checked both by
  count and by refusing self-targeted demotion/deletion;
* deleting a user cascades -- sessions revoked, API keys revoked, audit written
  -- immediately, not at next token expiry (Section 6.7).
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
    NotFoundError,
    UserAlreadyExistsError,
)
from identity_service.domain.policies.roles import (
    RoleChange,
    apply_role_change,
    assert_not_self_target,
    assert_tenant_keeps_an_org_admin,
    validate_role_keys,
)
from identity_service.domain.value_objects.tokens import generate_token, hash_token
from identity_service.infrastructure.db.models import Invitation
from identity_service.infrastructure.db.repositories.identity_repository import (
    IdentityRepository,
)
from identity_service.infrastructure.email.sender import (
    EmailSender,
    build_invitation_email,
)
from platform_auth.permissions import ROLE_ORG_ADMIN


@dataclass(frozen=True)
class IssuedInvitation:
    invitation: Invitation
    #: Raw token. Emailed, never persisted, never logged, never returned by the API.
    token: str


@dataclass(frozen=True)
class RoleChangeOutcome:
    before: frozenset[str]
    after: frozenset[str]

    @property
    def changed(self) -> bool:
        return self.before != self.after


class UserService:
    def __init__(
        self,
        *,
        repository: IdentityRepository,
        sessions: SessionService,
        audit: AuditService,
        email: EmailSender,
        settings: Settings,
    ) -> None:
        self._repository = repository
        self._sessions = sessions
        self._audit = audit
        self._email = email
        self._settings = settings

    # --- invitations ------------------------------------------------------

    async def invite(
        self,
        *,
        tenant_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        email: str,
        role_key: str,
        ip_address: str | None = None,
    ) -> IssuedInvitation:
        """Invite a user by email and role (Section 6.7)."""
        validate_role_keys(frozenset({role_key}))

        if await self._repository.find_user_by_email(tenant_id, email) is not None:
            raise UserAlreadyExistsError()

        tenant = await self._repository.get_tenant(tenant_id)
        if tenant is None:
            raise NotFoundError()

        token = generate_token()
        expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(days=self._settings.invitation_ttl_days)
        invitation = await self._repository.add_invitation(
            Invitation(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                email=email,
                role_key=role_key,
                token_hash=hash_token(token),
                invited_by=actor_user_id,
                status="pending",
                expires_at=expires_at,
            )
        )
        await self._audit.record(
            event_type=events.EVENT_USER_INVITED,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            resource_type="invitation",
            resource_id=str(invitation.id),
            after_state={"email": email, "role_key": role_key, "expires_at": str(expires_at)},
            ip_address=ip_address,
        )
        await self._email.send(
            build_invitation_email(
                to=email,
                tenant_name=tenant.name,
                role_key=role_key,
                accept_url=self._settings.invitation_accept_url,
                token=token,
                expires_in_days=self._settings.invitation_ttl_days,
            )
        )
        return IssuedInvitation(invitation=invitation, token=token)

    # --- roles -------------------------------------------------------------

    async def change_roles(
        self,
        *,
        tenant_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        target_user_id: uuid.UUID,
        grant: frozenset[str],
        revoke: frozenset[str],
        ip_address: str | None = None,
    ) -> RoleChangeOutcome:
        """Grant and revoke roles, upholding the Section 2 last-admin rule."""
        target = await self._repository.get_user(tenant_id, target_user_id)
        if target is None:
            raise NotFoundError()

        current = await self._repository.get_user_role_keys(target_user_id)
        resulting = apply_role_change(current, RoleChange(grant=grant, revoke=revoke))

        if ROLE_ORG_ADMIN in current and ROLE_ORG_ADMIN not in resulting:
            assert_not_self_target(
                actor_user_id=str(actor_user_id),
                target_user_id=str(target_user_id),
                operation="remove the organization administrator role from",
            )
            assert_tenant_keeps_an_org_admin(
                org_admin_count=await self._repository.count_active_org_admins(tenant_id),
                target_is_org_admin=True,
                target_remains_org_admin=False,
            )

        for key in resulting - current:
            role = await self._repository.ensure_role(tenant_id, key)
            await self._repository.grant_role(
                user_id=target_user_id, role_id=role.id, granted_by=actor_user_id
            )
        for key in current - resulting:
            role = await self._repository.ensure_role(tenant_id, key)
            await self._repository.revoke_role(user_id=target_user_id, role_id=role.id)

        await self._audit.record(
            event_type=events.EVENT_ROLE_CHANGED,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            resource_type="user",
            resource_id=str(target_user_id),
            before_state={"roles": sorted(current)},
            after_state={"roles": sorted(resulting)},
            ip_address=ip_address,
        )
        return RoleChangeOutcome(before=current, after=resulting)

    # --- deletion ----------------------------------------------------------

    async def delete_user(
        self,
        *,
        tenant_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        target_user_id: uuid.UUID,
        ip_address: str | None = None,
    ) -> None:
        """Deactivate a user and cascade the revocations (Section 6.7).

        The row is retained as `deactivated` rather than deleted, because
        `audit_events.actor_user_id` and `api_keys.created_by` reference it and
        an append-only audit trail must keep pointing at a real actor.
        """
        target = await self._repository.get_user(tenant_id, target_user_id)
        if target is None:
            raise NotFoundError()

        assert_not_self_target(
            actor_user_id=str(actor_user_id),
            target_user_id=str(target_user_id),
            operation="delete",
        )
        roles = await self._repository.get_user_role_keys(target_user_id)
        assert_tenant_keeps_an_org_admin(
            org_admin_count=await self._repository.count_active_org_admins(tenant_id),
            target_is_org_admin=ROLE_ORG_ADMIN in roles,
            target_remains_org_admin=False,
        )

        now = dt.datetime.now(dt.UTC)
        await self._repository.set_user_status(tenant_id, target_user_id, "deactivated")
        revoked_sessions = await self._sessions.revoke_all_for_user(tenant_id, target_user_id)
        revoked_keys = await self._repository.revoke_api_keys_for_owner(
            tenant_id, target_user_id, now
        )
        await self._audit.record(
            event_type=events.EVENT_USER_DELETED,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            resource_type="user",
            resource_id=str(target_user_id),
            before_state={"status": target.status, "roles": sorted(roles)},
            after_state={
                "status": "deactivated",
                "sessions_revoked": revoked_sessions,
                "api_keys_revoked": revoked_keys,
            },
            ip_address=ip_address,
        )

    async def force_revoke_sessions(
        self,
        *,
        tenant_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        target_user_id: uuid.UUID,
        ip_address: str | None = None,
    ) -> int:
        """Section 6.9: an `org_admin` can force-logout a user in their tenant."""
        if await self._repository.get_user(tenant_id, target_user_id) is None:
            raise NotFoundError()
        count = await self._sessions.revoke_all_for_user(tenant_id, target_user_id)
        await self._audit.record(
            event_type=events.EVENT_SESSIONS_REVOKED_FOR_USER,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            resource_type="user",
            resource_id=str(target_user_id),
            after_state={"sessions_revoked": count},
            ip_address=ip_address,
        )
        return count
