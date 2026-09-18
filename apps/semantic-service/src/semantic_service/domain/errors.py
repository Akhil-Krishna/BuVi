"""API errors (Section 21)."""

from __future__ import annotations

from platform_observability.errors import ApiError


class DomainError(ApiError):
    code: str = "INTERNAL_ERROR"
    status_code: int = 500
    message: str = "An unexpected error occurred."


class NotFoundError(DomainError):
    code = "NOT_FOUND"
    status_code = 404
    message = "Not found."


class AuthenticationRequiredError(DomainError):
    code = "AUTHENTICATION_REQUIRED"
    status_code = 401
    message = "Authentication required."


class UserNotActiveError(DomainError):
    code = "USER_NOT_ACTIVE"
    status_code = 403
    message = "This account is not active."


class InvalidCursorError(DomainError):
    code = "INVALID_CURSOR"
    status_code = 422
    message = "The pagination cursor is invalid."


class DefinitionInvalidError(DomainError):
    """The expression or catalog reference does not satisfy Section 8.3's rules."""

    code = "SEMANTIC_DEFINITION_INVALID"
    status_code = 422
    message = "The definition is not valid against the catalog."


class NameTakenError(DomainError):
    code = "SEMANTIC_NAME_TAKEN"
    status_code = 409
    message = "A definition with this name already exists."


class InvalidTransitionError(DomainError):
    code = "INVALID_STATUS_TRANSITION"
    status_code = 409
    message = "The metric cannot move to that status from its current one."


class ContextReaderNotAllowedError(DomainError):
    code = "CONTEXT_READER_NOT_ALLOWED"
    status_code = 403
    message = "This caller may not read the semantic context."


class UpstreamUnavailableError(DomainError):
    code = "UPSTREAM_UNAVAILABLE"
    status_code = 502
    message = "A backing service is unavailable."


class UpstreamTimeoutError(DomainError):
    code = "UPSTREAM_TIMEOUT"
    status_code = 504
    message = "A backing service did not respond in time."
