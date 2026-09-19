"""Domain errors and their stable API error codes (Section 21).

Every error the API can return has a code here. Clients branch on
`error.code`, never on `error.message` -- messages are for humans and may be
reworded without a contract change (Section 21).

A domain error carries no stack trace, no SQL, no credential and no provider
detail; the full diagnostic is logged server-side against the `request_id`.
"""

from __future__ import annotations

from platform_observability.errors import ApiError


class DomainError(ApiError):
    """Base class for every error the API maps to an error envelope.

    `ApiError` is plain Python (no FastAPI import), so the domain layer stays
    framework-free while every service shares one envelope (Section 21).
    """

    code: str = "INTERNAL_ERROR"
    status_code: int = 500
    message: str = "An unexpected error occurred."


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


class MfaChallengeRequiredError(DomainError):
    """A WebAuthn response arrived with no live challenge for this session."""

    code = "MFA_CHALLENGE_REQUIRED"
    status_code = 409
    message = "Request a new challenge and try again."


# --- Invitations (Section 6.7) -----------------------------------------------


class InvitationInvalidError(DomainError):
    """Covers unknown, already-used, revoked, and expired tokens alike.

    One code for all four so a caller cannot probe which invitations exist.
    """

    code = "INVITATION_INVALID"
    status_code = 400
    message = "This invitation is no longer valid."


class InvitationEmailMismatchError(DomainError):
    """The IdP identity redeeming an invitation is not the invited address (Section 6.7)."""

    code = "INVITATION_EMAIL_MISMATCH"
    status_code = 403
    message = "This invitation was sent to a different email address."


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


# --- Service-to-service auth (Section 6.3) ------------------------------------


class InvalidServiceClientError(DomainError):
    code = "INVALID_SERVICE_CLIENT"
    status_code = 401
    message = "Service client authentication failed."


class ServiceGrantNotAllowedError(DomainError):
    """The client may not obtain a token for that audience or scope."""

    code = "SERVICE_GRANT_NOT_ALLOWED"
    status_code = 403
    message = "This service client may not obtain that token."


class UnsupportedGrantTypeError(DomainError):
    code = "UNSUPPORTED_GRANT_TYPE"
    status_code = 400
    message = "Only the client_credentials grant is supported."


class AuditEventNotAllowedError(DomainError):
    """A service client tried to record an audit event outside its registered namespace."""

    code = "AUDIT_EVENT_NOT_ALLOWED"
    status_code = 403
    message = "This service client may not record that audit event."


class WebAuthnNotEnrolledError(DomainError):
    """Requiring WebAuthn of org_admins while the acting admin has no key would lock them out."""

    code = "WEBAUTHN_NOT_ENROLLED"
    status_code = 409
    message = "Register a security key before requiring one for administrators."
