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


class ForbiddenError(DomainError):
    code = "FORBIDDEN"
    status_code = 403
    message = "You do not have permission to perform this action."


class InvalidCursorError(DomainError):
    code = "INVALID_CURSOR"
    status_code = 422
    message = "The pagination cursor is invalid."


class WebhookUrlInvalidError(DomainError):
    """The URL breaks a Section 15 rule; `details.reason` says which."""

    code = "WEBHOOK_URL_INVALID"
    status_code = 422
    message = "The webhook URL is not allowed."


class WebhookEventTypeNotAllowedError(DomainError):
    code = "WEBHOOK_EVENT_TYPE_NOT_ALLOWED"
    status_code = 422
    message = "One or more event types cannot be delivered by webhook."


class WebhookLimitError(DomainError):
    code = "WEBHOOK_LIMIT_REACHED"
    status_code = 409
    message = "This organization has reached its active webhook limit."


class SecretStoreUnavailableError(DomainError):
    code = "SECRET_STORE_UNAVAILABLE"
    status_code = 503
    message = "The secret store is unavailable. Try again shortly."


class UpstreamUnavailableError(DomainError):
    code = "UPSTREAM_UNAVAILABLE"
    status_code = 502
    message = "A backing service is unavailable."


class UpstreamTimeoutError(DomainError):
    code = "UPSTREAM_TIMEOUT"
    status_code = 504
    message = "A backing service did not respond in time."
