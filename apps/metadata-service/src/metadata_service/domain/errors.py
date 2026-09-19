"""Domain errors and their stable API error codes (Section 21).

Clients branch on `error.code`, never on `error.message`. No error carries a host,
user, driver message, Vault path, or credential.
"""

from __future__ import annotations

from platform_observability.errors import ApiError


class DomainError(ApiError):
    code: str = "INTERNAL_ERROR"
    status_code: int = 500
    message: str = "An unexpected error occurred."


class NotFoundError(DomainError):
    """The resource does not exist, or belongs to another tenant (Section 7.2)."""

    code = "NOT_FOUND"
    status_code = 404
    message = "Not found."


class ValidationFailedError(DomainError):
    code = "VALIDATION_FAILED"
    status_code = 422
    message = "The request payload is invalid."


class AuthenticationRequiredError(DomainError):
    code = "AUTHENTICATION_REQUIRED"
    status_code = 401
    message = "Authentication required."


class UserNotActiveError(DomainError):
    code = "USER_NOT_ACTIVE"
    status_code = 403
    message = "This account is not active."


class UpstreamUnavailableError(DomainError):
    code = "UPSTREAM_UNAVAILABLE"
    status_code = 502
    message = "A backing service is unavailable."


class UpstreamTimeoutError(DomainError):
    code = "UPSTREAM_TIMEOUT"
    status_code = 504
    message = "A backing service did not respond in time."


class EngineNotSupportedError(DomainError):
    """The engine is valid for Section 8.2 but has no connector yet (Phase A8)."""

    code = "ENGINE_NOT_SUPPORTED"
    status_code = 422
    message = "This database engine is not supported yet."


class DestinationNotAllowedError(DomainError):
    """Section 15: a private, loopback or link-local destination outside the allow-list."""

    code = "DESTINATION_NOT_ALLOWED"
    status_code = 422
    message = "This destination is not allowed for data-source connections."


class SecretNotConfiguredError(DomainError):
    code = "SECRET_NOT_CONFIGURED"
    status_code = 409
    message = "Set the connection credentials before testing or syncing this data source."


class DataSourceDisabledError(DomainError):
    code = "DATA_SOURCE_DISABLED"
    status_code = 409
    message = "This data source is disabled."


class SecretStoreUnavailableError(DomainError):
    code = "SECRET_STORE_UNAVAILABLE"
    status_code = 503
    message = "The secret store is unavailable. Retry later."


class InvalidCursorError(DomainError):
    code = "INVALID_CURSOR"
    status_code = 422
    message = "The pagination cursor is invalid."


class ForbiddenError(DomainError):
    code = "FORBIDDEN"
    status_code = 403
    message = "You do not have permission to perform this action."


class SqlGrantExistsError(DomainError):
    code = "SQL_GRANT_EXISTS"
    status_code = 409
    message = "This user already has SQL access to the data source."
