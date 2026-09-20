"""Audit writing (Sections 8.1, 22, 24).

Section 22 lists what must be audited: login/logout, MFA changes, role changes,
permission changes, and every admin action. This service is the only writer, so
that two rules hold everywhere at once:

* `before_state`/`after_state` pass through `redact` first -- Section 24 forbids
  a secret appearing in an audit diff, and an admin endpoint that echoed its own
  request body would otherwise do exactly that;
* every row carries the `request_id` of the request that caused it, so an audit
  entry joins to the logs and traces for the same operation (Section 22).
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from identity_service.core.logging import redact, request_id_var
from identity_service.infrastructure.db.independent import IndependentWrites
from identity_service.infrastructure.db.models import AuditEvent
from identity_service.infrastructure.db.repositories.identity_repository import (
    IdentityRepository,
)

# --- Event types -------------------------------------------------------------
# Stable strings; the admin audit API filters on them and Phase B7 renders them.

EVENT_LOGIN: Final = "auth.login"
EVENT_LOGIN_FAILED: Final = "auth.login_failed"
EVENT_LOGOUT: Final = "auth.logout"
EVENT_SESSION_REVOKED: Final = "auth.session_revoked"
EVENT_SESSIONS_REVOKED_FOR_USER: Final = "auth.sessions_revoked_for_user"
EVENT_MFA_ENROLL_STARTED: Final = "auth.mfa_enroll_started"
EVENT_MFA_ENABLED: Final = "auth.mfa_enabled"
EVENT_MFA_VERIFIED: Final = "auth.mfa_verified"
EVENT_MFA_VERIFICATION_FAILED: Final = "auth.mfa_verification_failed"
EVENT_MFA_FACTOR_ADDED: Final = "auth.mfa_factor_added"
EVENT_MFA_FACTOR_REMOVED: Final = "auth.mfa_factor_removed"
EVENT_MFA_RESET: Final = "auth.mfa_reset"
EVENT_POLICIES_CHANGED: Final = "tenant.policies_changed"
EVENT_USER_INVITED: Final = "user.invited"
EVENT_INVITATION_ACCEPTED: Final = "user.invitation_accepted"
EVENT_USER_PROVISIONED: Final = "user.provisioned"
EVENT_USER_DELETED: Final = "user.deleted"
EVENT_ROLE_CHANGED: Final = "user.role_changed"
EVENT_API_KEY_CREATED: Final = "api_key.created"
EVENT_API_KEY_REVOKED: Final = "api_key.revoked"

# Events about a refusal. The refusal rolls its request back, so these may only be written by
# `record_failure`, in their own transaction; `record` refuses them (a Phase A10 bug lost them).
REFUSAL_EVENTS: Final = frozenset({EVENT_LOGIN_FAILED, EVENT_MFA_VERIFICATION_FAILED})


class AuditWiringError(RuntimeError):
    """A programming error: a refusal event on a path that would roll it back."""


class AuditService:
    """Append-only writer for `identity.audit_events`."""

    def __init__(
        self, repository: IdentityRepository, independent: IndependentWrites | None = None
    ) -> None:
        self._repository = repository
        self._independent = independent

    async def record_failure(self, **event: Any) -> None:
        """Record an event about a refusal, in its own transaction, so it survives the
        rollback the refusal causes (a failed MFA check, a refused login)."""
        if self._independent is None:
            # No silent fallback to the request transaction: that is the bug this prevents.
            raise AuditWiringError("record_failure needs IndependentWrites")

        async def write(repository: IdentityRepository) -> None:
            await AuditService(repository)._append(**event)

        await self._independent.run(event["tenant_id"], write)

    async def record(self, *, event_type: str, **event: Any) -> None:
        """Append one audit event, with secret-shaped fields redacted, in the request's
        transaction (so it commits only if the request does)."""
        if event_type in REFUSAL_EVENTS:
            raise AuditWiringError(f"{event_type} must be written with record_failure")
        await self._append(event_type=event_type, **event)

    async def _append(
        self,
        *,
        event_type: str,
        tenant_id: uuid.UUID | None,
        actor_user_id: uuid.UUID | None = None,
        actor_type: str = "user",
        acting_as_tenant_id: uuid.UUID | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        before_state: dict[str, Any] | None = None,
        after_state: dict[str, Any] | None = None,
        ip_address: str | None = None,
    ) -> None:
        event = AuditEvent(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            actor_type=actor_type,
            acting_as_tenant_id=acting_as_tenant_id,
            event_type=event_type,
            resource_type=resource_type,
            resource_id=resource_id,
            before_state=redact(before_state) if before_state is not None else None,
            after_state=redact(after_state) if after_state is not None else None,
            request_id=request_id_var.get(),
            ip_address=ip_address,
        )
        await self._repository.add_audit_event(event)
