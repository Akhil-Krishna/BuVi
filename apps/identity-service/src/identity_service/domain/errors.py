"""Domain errors and their stable API error codes (Section 21).

Every error the API can return has a code here. Clients branch on
`error.code`, never on `error.message` -- messages are for humans and may be
reworded without a contract change (Section 21).

A domain error carries no stack trace, no SQL, no credential and no provider
detail; the full diagnostic is logged server-side against the `request_id`.
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base class for every error the API maps to an error envelope."""

    code: str = "INTERNAL_ERROR"
    status_code: int = 500
    message: str = "An unexpected error occurred."

    def __init__(self, message: str | None = None, **details: Any) -> None:
        super().__init__(message or self.message)
        self.detail_message = message or self.message
        self.details: dict[str, Any] = details


# --- Generic ----------------------------------------------------------------


class NotFoundError(DomainError):
    """The resource does not exist, or belongs to another tenant.

    These two cases are deliberately indistinguishable (Section 7.2).
    """

    code = "NOT_FOUND"
    status_code = 404
    message = "Not found."


class ValidationFailedError(DomainError):
    code = "VALIDATION_FAILED"
    status_code = 422
    message = "The request payload is invalid."


class ConflictError(DomainError):
    code = "CONFLICT"
    status_code = 409
    message = "The resource is in a conflicting state."


# --- Authentication (Section 6) ---------------------------------------------


class AuthenticationRequiredError(DomainError):
    code = "AUTHENTICATION_REQUIRED"
    status_code = 401
    message = "Authentication required."


class SessionExpiredError(DomainError):
    code = "SESSION_EXPIRED"
    status_code = 401
    message = "The session has expired or been revoked."


class OidcExchangeFailedError(DomainError):
    """The IdP rejected the code exchange.

    The upstream reason is logged, never returned: an IdP error body can leak
    client configuration and realm detail (Section 21).
    """

    code = "OIDC_EXCHANGE_FAILED"
    status_code = 401
    message = "Sign-in could not be completed."


class OidcStateMismatchError(DomainError):
    code = "OIDC_STATE_MISMATCH"
    status_code = 400
    message = "The sign-in request could not be verified. Start again."


class UserNotActiveError(DomainError):
    code = "USER_NOT_ACTIVE"
    status_code = 403
    message = "This account is not active."


# --- MFA (Section 6.6) -------------------------------------------------------


class MfaAlreadyEnrolledError(DomainError):
    code = "MFA_ALREADY_ENROLLED"
    status_code = 409
    message = "Multi-factor authentication is already enabled for this account."


class MfaNotEnrolledError(DomainError):
    code = "MFA_NOT_ENROLLED"
    status_code = 409
    message = "Multi-factor authentication is not enrolled for this account."


class MfaVerificationFailedError(DomainError):
    code = "MFA_VERIFICATION_FAILED"
    status_code = 401
    message = "The verification code is incorrect or has expired."


class StepUpRequiredError(DomainError):
    """A Section 7.3 sensitive operation without a recent MFA verification."""

    code = "STEP_UP_REQUIRED"
    status_code = 403
    message = "This operation requires re-verifying your identity."


# --- Invitations (Section 6.7) -----------------------------------------------


class InvitationInvalidError(DomainError):
    """Covers unknown, already-used, revoked, and expired tokens alike.

    One code for all four so a caller cannot probe which invitations exist.
    """

    code = "INVITATION_INVALID"
    status_code = 400
    message = "This invitation is no longer valid."


class UserAlreadyExistsError(DomainError):
    code = "USER_ALREADY_EXISTS"
    status_code = 409
    message = "A user with that email already exists in this organization."


# --- Roles and lifecycle (Section 2) -----------------------------------------


class LastOrgAdminError(DomainError):
    """Section 2: a tenant MUST always retain at least one `org_admin`."""

    code = "LAST_ORG_ADMIN"
    status_code = 409
    message = "A tenant must keep at least one organization administrator."


class UnknownRoleError(DomainError):
    code = "UNKNOWN_ROLE"
    status_code = 422
    message = "That role does not exist."


class SelfServiceForbiddenError(DomainError):
    """An admin operation an actor may not perform on their own account."""

    code = "SELF_SERVICE_FORBIDDEN"
    status_code = 409
    message = "You cannot perform this operation on your own account."


# --- API keys (Section 6.8) --------------------------------------------------


class ApiKeyRevokedError(DomainError):
    code = "API_KEY_REVOKED"
    status_code = 401
    message = "This API key has been revoked or has expired."
