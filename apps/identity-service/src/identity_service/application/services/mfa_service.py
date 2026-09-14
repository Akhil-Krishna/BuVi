"""TOTP multi-factor authentication (Section 6.6).

TOTP first; WebAuthn follows in Phase A10. Two properties are worth naming:

* the shared secret lives in Vault, never in Postgres -- `mfa_credentials`
  stores only a `secret_ref` (Section 24);
* an accepted code cannot be replayed. TOTP codes stay valid for a whole step
  (plus the drift window), so `last_used_step` records the highest step already
  accepted and anything at or below it is refused.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

import pyotp

from identity_service.application.services import audit_service as events
from identity_service.application.services.audit_service import AuditService
from identity_service.core.config import Settings
from identity_service.domain.errors import (
    MfaAlreadyEnrolledError,
    MfaNotEnrolledError,
    MfaVerificationFailedError,
)
from identity_service.infrastructure.db.models import MfaCredential, User
from identity_service.infrastructure.db.repositories.identity_repository import (
    IdentityRepository,
)
from identity_service.infrastructure.secrets.store import SecretStore, mfa_secret_ref

TOTP_STEP_SECONDS = 30


@dataclass(frozen=True)
class EnrollmentChallenge:
    """Returned once, at enrolment. The URI embeds the shared secret."""

    secret: str
    provisioning_uri: str


class MfaService:
    def __init__(
        self,
        *,
        repository: IdentityRepository,
        secrets: SecretStore,
        audit: AuditService,
        settings: Settings,
    ) -> None:
        self._repository = repository
        self._secrets = secrets
        self._audit = audit
        self._settings = settings

    async def begin_enrollment(self, user: User) -> EnrollmentChallenge:
        """Generate and store a TOTP secret, unconfirmed until first verify.

        Re-enrolling while MFA is already enabled is refused: that path is a
        Section 7.3 step-up operation and is handled by the reset flow in Phase
        A10, not by silently replacing a working credential here.
        """
        existing = await self._repository.get_mfa_credential(user.tenant_id, user.id)
        if existing is not None and existing.confirmed_at is not None:
            raise MfaAlreadyEnrolledError()
        if existing is not None:
            # An abandoned, unconfirmed enrolment is safe to replace.
            await self._repository.delete_mfa_credential(existing.id)

        secret = pyotp.random_base32()
        ref = mfa_secret_ref(user.tenant_id, user.id)
        await self._secrets.write(ref, {"secret": secret})
        await self._repository.add_mfa_credential(
            MfaCredential(
                id=uuid.uuid4(),
                tenant_id=user.tenant_id,
                user_id=user.id,
                method="totp",
                secret_ref=ref,
            )
        )
        await self._audit.record(
            event_type=events.EVENT_MFA_ENROLL_STARTED,
            tenant_id=user.tenant_id,
            actor_user_id=user.id,
            resource_type="user",
            resource_id=str(user.id),
            after_state={"method": "totp"},
        )
        uri = pyotp.TOTP(secret).provisioning_uri(
            name=user.email, issuer_name=self._settings.totp_issuer
        )
        return EnrollmentChallenge(secret=secret, provisioning_uri=uri)

    async def verify(self, *, user: User, code: str) -> dt.datetime:
        """Verify a TOTP code, confirming enrolment on first success.

        Returns the verification time, which starts the Section 7.3 step-up
        window for the caller's session.
        """
        credential = await self._repository.get_mfa_credential(user.tenant_id, user.id)
        if credential is None:
            raise MfaNotEnrolledError()

        stored = await self._secrets.read(credential.secret_ref)
        if not stored or "secret" not in stored:
            # The reference outlived the secret; treat as not enrolled rather
            # than leaking that the Vault entry is missing.
            raise MfaNotEnrolledError()

        totp = pyotp.TOTP(stored["secret"])
        if not totp.verify(code, valid_window=self._settings.totp_valid_window):
            await self._audit.record(
                event_type=events.EVENT_MFA_VERIFICATION_FAILED,
                tenant_id=user.tenant_id,
                actor_user_id=user.id,
                resource_type="user",
                resource_id=str(user.id),
            )
            raise MfaVerificationFailedError()

        now = dt.datetime.now(dt.UTC)
        step = int(now.timestamp()) // TOTP_STEP_SECONDS
        last_step = (
            int(credential.last_used_at.timestamp()) // TOTP_STEP_SECONDS
            if credential.last_used_at is not None
            else None
        )
        if last_step is not None and step <= last_step:
            # Correct code, already spent. Refused so a captured code cannot be
            # replayed inside its validity window.
            await self._audit.record(
                event_type=events.EVENT_MFA_VERIFICATION_FAILED,
                tenant_id=user.tenant_id,
                actor_user_id=user.id,
                resource_type="user",
                resource_id=str(user.id),
                after_state={"reason": "code_replayed"},
            )
            raise MfaVerificationFailedError()

        newly_confirmed = credential.confirmed_at is None
        if newly_confirmed:
            await self._repository.confirm_mfa_credential(credential.id, at=now)
            await self._repository.set_mfa_enabled(user.tenant_id, user.id, enabled=True)
            await self._audit.record(
                event_type=events.EVENT_MFA_ENABLED,
                tenant_id=user.tenant_id,
                actor_user_id=user.id,
                resource_type="user",
                resource_id=str(user.id),
                before_state={"mfa_enabled": False},
                after_state={"mfa_enabled": True, "method": "totp"},
            )
        else:
            await self._repository.record_mfa_use(credential.id, now)
            await self._audit.record(
                event_type=events.EVENT_MFA_VERIFIED,
                tenant_id=user.tenant_id,
                actor_user_id=user.id,
                resource_type="user",
                resource_id=str(user.id),
            )
        return now
