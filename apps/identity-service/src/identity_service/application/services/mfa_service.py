"""Multi-factor authentication: TOTP and WebAuthn (Sections 6.6, 7.3; Phases A1, A10).

* **Secret material lives in Vault**, never in Postgres. `mfa_credentials` holds only a
  `secret_ref`. For TOTP that is the shared secret; for WebAuthn, the credential id, public
  key and signature counter (Section 8.1).
* **An accepted TOTP code cannot be replayed.** `last_used_at` records the step already
  accepted, and anything at or below it is refused.
* **A WebAuthn challenge is single-use and bound to its session and ceremony.** It is
  stored on the session with its purpose (`reg:` or `auth:`), taken and cleared in one step,
  and short-lived. A cloned key shows up as a signature-counter regression, and the library
  refuses it.
* **Only the first factor is free** (Section 6.6). Adding another, removing one, or resetting
  someone else's all need a fresh step-up with an existing factor.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import pyotp
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

from identity_service.application.services import audit_service as events
from identity_service.application.services.audit_service import AuditService
from identity_service.core.config import Settings
from identity_service.domain.errors import (
    MfaAlreadyEnrolledError,
    MfaChallengeRequiredError,
    MfaNotEnrolledError,
    MfaTooManyAttemptsError,
    MfaVerificationFailedError,
    NotFoundError,
)
from identity_service.infrastructure.db.independent import IndependentWrites
from identity_service.infrastructure.db.models import MfaCredential, Session, User
from identity_service.infrastructure.db.repositories.identity_repository import (
    IdentityRepository,
)
from identity_service.infrastructure.secrets.store import (
    SecretStore,
    mfa_secret_ref,
    webauthn_key_ref,
)
from identity_service.infrastructure.webauthn.relying_party import (
    CeremonyFailedError,
    RelyingParty,
    credential_id_of,
)
from platform_auth import Principal, StepUpRequiredError
from platform_auth.principal import MfaMethod

TOTP_STEP_SECONDS = 30
_REGISTRATION = "reg:"
_ASSERTION = "auth:"


@dataclass(frozen=True)
class Enrollment:
    """Returned once. For TOTP the URI embeds the shared secret; for WebAuthn, the options
    for `navigator.credentials.create`."""

    method: Literal["totp", "webauthn"]
    secret: str | None = None
    provisioning_uri: str | None = None
    options: dict[str, Any] | None = None


class MfaService:
    def __init__(
        self,
        *,
        repository: IdentityRepository,
        secrets: SecretStore,
        audit: AuditService,
        settings: Settings,
        relying_party: RelyingParty,
        independent: IndependentWrites,
    ) -> None:
        self._independent = independent
        self._repository = repository
        self._secrets = secrets
        self._audit = audit
        self._settings = settings
        self._rp = relying_party

    # --- enrollment ------------------------------------------------------------------

    async def begin_enrollment(
        self,
        *,
        user: User,
        principal: Principal,
        session_row: Session,
        method: Literal["totp", "webauthn"],
    ) -> Enrollment:
        factors = await self._repository.list_mfa_credentials(
            user.tenant_id, user.id, confirmed_only=True
        )
        # Section 6.6: only the first factor is enrolled on the session alone. Any fresh factor
        # may add a WebAuthn key -- the stronger factor must be reachable from the weaker one.
        if factors and not principal.step_up_is_fresh(any_method=method == "webauthn"):
            raise StepUpRequiredError.for_principal(principal)
        if method == "totp":
            return await self._begin_totp(user, has_totp=any(f.method == "totp" for f in factors))
        keys = [await self._key(f) for f in factors if f.method == "webauthn"]
        options = self._rp.registration_options(
            user_id=user.id.bytes,
            user_name=str(user.email),
            display_name=user.display_name,
            exclude=[base64url_to_bytes(k["credential_id"]) for k in keys if k],
        )
        await self._set_challenge(session_row, _REGISTRATION, options.challenge)
        await self._record(events.EVENT_MFA_ENROLL_STARTED, user, after={"method": "webauthn"})
        return Enrollment(method="webauthn", options=options.public_key)

    async def _begin_totp(self, user: User, *, has_totp: bool) -> Enrollment:
        """At most one TOTP factor (Section 8.1). An abandoned, unconfirmed one is replaced."""
        if has_totp:
            raise MfaAlreadyEnrolledError()
        existing = await self._repository.get_mfa_credential(user.tenant_id, user.id)
        if existing is not None:
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
        await self._record(events.EVENT_MFA_ENROLL_STARTED, user, after={"method": "totp"})
        uri = pyotp.TOTP(secret).provisioning_uri(
            name=user.email, issuer_name=self._settings.totp_issuer
        )
        return Enrollment(method="totp", secret=secret, provisioning_uri=uri)

    # --- TOTP ----------------------------------------------------------------------------

    async def verify_totp(self, *, user: User, code: str) -> dt.datetime:
        """Verify a TOTP code, confirming enrollment on first success. Returns the time the
        Section 7.3 step-up window starts."""
        await self._throttle(user)
        credential = await self._repository.get_mfa_credential(user.tenant_id, user.id)
        if credential is None:
            raise MfaNotEnrolledError()
        stored = await self._secrets.read(credential.secret_ref)
        if not stored or "secret" not in stored:
            # The reference outlived the secret; treat as not enrolled rather than leaking
            # that the Vault entry is missing.
            raise MfaNotEnrolledError()
        if not pyotp.TOTP(stored["secret"]).verify(
            code, valid_window=self._settings.totp_valid_window
        ):
            await self._failure(user)
            raise MfaVerificationFailedError()

        now = dt.datetime.now(dt.UTC)
        step = int(now.timestamp()) // TOTP_STEP_SECONDS
        if (
            credential.last_used_at is not None
            and step <= int(credential.last_used_at.timestamp()) // TOTP_STEP_SECONDS
        ):
            # Correct code, already spent: refused so a captured code cannot be replayed.
            await self._failure(user, {"reason": "code_replayed"})
            raise MfaVerificationFailedError()
        if credential.confirmed_at is None:
            await self._confirm(user, credential.id, "totp", now)
        else:
            await self._repository.record_mfa_use(credential.id, now)
            await self._record(events.EVENT_MFA_VERIFIED, user, after={"method": "totp"})
        return now

    # --- WebAuthn ------------------------------------------------------------------------

    async def begin_assertion(self, *, user: User, session_row: Session) -> dict[str, Any]:
        """Options for `navigator.credentials.get` over the user's registered keys."""
        keys = [
            key
            for f in await self._repository.list_mfa_credentials(
                user.tenant_id, user.id, method="webauthn", confirmed_only=True
            )
            if (key := await self._key(f))
        ]
        if not keys:
            raise MfaNotEnrolledError()
        options = self._rp.authentication_options(
            allow=[base64url_to_bytes(k["credential_id"]) for k in keys]
        )
        await self._set_challenge(session_row, _ASSERTION, options.challenge)
        return options.public_key

    async def verify_webauthn(
        self,
        *,
        user: User,
        session_row: Session,
        credential: dict[str, Any],
        label: str | None,
    ) -> dt.datetime:
        """Complete a registration (new key) or an assertion (step-up), whichever the
        session's pending challenge was issued for."""
        # Spent in its own transaction: a failed attempt must not leave it reusable.
        stored, expires_at = await self._independent.run(
            user.tenant_id, lambda repository: repository.take_webauthn_challenge(session_row.id)
        )
        now = dt.datetime.now(dt.UTC)
        if stored is None or expires_at is None or expires_at <= now:
            raise MfaChallengeRequiredError()
        purpose, _, encoded = stored.partition(":")
        challenge = base64url_to_bytes(encoded)
        try:
            if f"{purpose}:" == _REGISTRATION:
                await self._register(user, credential, challenge, label, now)
            else:
                await self._assert(user, credential, challenge, now)
        except CeremonyFailedError:
            await self._failure(user, {"method": "webauthn"})
            raise MfaVerificationFailedError() from None
        return now

    async def _register(
        self,
        user: User,
        credential: dict[str, Any],
        challenge: bytes,
        label: str | None,
        now: dt.datetime,
    ) -> None:
        key = self._rp.verify_registration(credential, challenge)
        row_id = uuid.uuid4()
        ref = webauthn_key_ref(user.tenant_id, user.id, row_id)
        await self._secrets.write(
            ref,
            {
                "credential_id": bytes_to_base64url(key.credential_id),
                "public_key": bytes_to_base64url(key.public_key),
                "sign_count": str(key.sign_count),
            },
        )
        await self._repository.add_mfa_credential(
            MfaCredential(
                id=row_id,
                tenant_id=user.tenant_id,
                user_id=user.id,
                method="webauthn",
                secret_ref=ref,
                label=(label or "Security key")[:100],
            )
        )
        await self._confirm(user, row_id, "webauthn", now)

    async def _assert(
        self, user: User, credential: dict[str, Any], challenge: bytes, now: dt.datetime
    ) -> None:
        presented = credential_id_of(credential)
        for factor in await self._repository.list_mfa_credentials(
            user.tenant_id, user.id, method="webauthn", confirmed_only=True
        ):
            key = await self._key(factor)
            if not key or key["credential_id"] != presented:
                continue
            count = self._rp.verify_assertion(
                credential,
                challenge,
                public_key=base64url_to_bytes(key["public_key"]),
                sign_count=int(key["sign_count"]),
            )
            await self._secrets.write(factor.secret_ref, {**key, "sign_count": str(count)})
            await self._repository.record_mfa_use(factor.id, now)
            await self._record(events.EVENT_MFA_VERIFIED, user, after={"method": "webauthn"})
            return
        raise CeremonyFailedError()

    # --- factors -------------------------------------------------------------------------

    async def factors(self, user: User) -> list[MfaCredential]:
        return await self._repository.list_mfa_credentials(
            user.tenant_id, user.id, confirmed_only=True
        )

    async def remove_factor(self, *, user: User, credential_id: uuid.UUID) -> None:
        """Remove one of the caller's own factors (step-up checked by the route)."""
        factor = await self._repository.get_mfa_credential_by_id(user.tenant_id, credential_id)
        if factor is None or factor.user_id != user.id:
            raise NotFoundError()
        await self._revoke(user, [factor])
        await self._record(
            events.EVENT_MFA_FACTOR_REMOVED,
            user,
            before={"factor_id": str(factor.id), "method": factor.method},
        )

    async def reset(self, *, actor: Principal, user: User) -> int:
        """An admin revokes every factor of another user (Section 7.3). Sessions are revoked
        by the caller, so the user re-enrolls from a fresh login."""
        factors = await self._repository.list_mfa_credentials(user.tenant_id, user.id)
        await self._revoke(user, factors)
        await self._audit.record(
            event_type=events.EVENT_MFA_RESET,
            tenant_id=user.tenant_id,
            actor_user_id=uuid.UUID(actor.user_id),
            resource_type="user",
            resource_id=str(user.id),
            before_state={"factors": len(factors)},
            after_state={"factors": 0, "mfa_enabled": False},
        )
        return len(factors)

    # --- helpers -------------------------------------------------------------------------

    async def _confirm(
        self, user: User, credential_id: uuid.UUID, method: MfaMethod, now: dt.datetime
    ) -> None:
        await self._repository.confirm_mfa_credential(credential_id, at=now)
        first = not user.mfa_enabled
        await self._repository.set_mfa_enabled(user.tenant_id, user.id, enabled=True)
        user.mfa_enabled = True
        await self._record(
            events.EVENT_MFA_ENABLED if first else events.EVENT_MFA_FACTOR_ADDED,
            user,
            before={"mfa_enabled": not first},
            after={"mfa_enabled": True, "method": method, "factor_id": str(credential_id)},
        )

    async def _revoke(self, user: User, factors: list[MfaCredential]) -> None:
        now = dt.datetime.now(dt.UTC)
        await self._repository.revoke_mfa_credentials(user.tenant_id, [f.id for f in factors], now)
        for factor in factors:  # a revoked factor keeps no secret material anywhere
            await self._secrets.delete(factor.secret_ref)
        remaining = await self._repository.list_mfa_credentials(
            user.tenant_id, user.id, confirmed_only=True
        )
        if not remaining:
            await self._repository.set_mfa_enabled(user.tenant_id, user.id, enabled=False)
            user.mfa_enabled = False

    async def _key(self, factor: MfaCredential) -> dict[str, str] | None:
        stored = await self._secrets.read(factor.secret_ref)
        return stored if stored and "credential_id" in stored else None

    async def _set_challenge(self, session_row: Session, purpose: str, challenge: bytes) -> None:
        expires = dt.datetime.now(dt.UTC) + dt.timedelta(
            seconds=self._settings.webauthn_challenge_seconds
        )
        await self._repository.set_webauthn_challenge(
            session_row.id, f"{purpose}{bytes_to_base64url(challenge)}", expires
        )

    async def _throttle(self, user: User) -> None:
        """Section 24: a per-account guess limit. The gateway's auth tier limits per IP; an
        attacker holding a session could spread TOTP guesses across addresses. The count comes
        from the durable failure audit events (committed even when the request rolls back), so
        it holds across replicas and restarts."""
        window = self._settings.mfa_failure_window_seconds
        since = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=window)
        failures = await self._repository.count_audit_events(
            user.tenant_id,
            actor_user_id=user.id,
            event_type=events.EVENT_MFA_VERIFICATION_FAILED,
            since=since,
        )
        if failures >= self._settings.mfa_max_failures:
            raise MfaTooManyAttemptsError(headers={"Retry-After": str(window)})

    async def _failure(self, user: User, after: dict[str, Any] | None = None) -> None:
        """A failed check is audited even though the request that failed rolls back."""
        await self._audit.record_failure(
            event_type=events.EVENT_MFA_VERIFICATION_FAILED,
            tenant_id=user.tenant_id,
            actor_user_id=user.id,
            resource_type="user",
            resource_id=str(user.id),
            after_state=after,
        )

    async def _record(
        self,
        event_type: str,
        user: User,
        *,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        await self._audit.record(
            event_type=event_type,
            tenant_id=user.tenant_id,
            actor_user_id=user.id,
            resource_type="user",
            resource_id=str(user.id),
            before_state=before,
            after_state=after,
        )
