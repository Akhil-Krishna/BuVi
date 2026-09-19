"""API errors (Section 21)."""

from __future__ import annotations

from platform_observability.errors import ApiError


class DomainError(ApiError):
    code: str = "INTERNAL_ERROR"
    status_code: int = 500
    message: str = "An unexpected error occurred."


class NotFoundError(DomainError):
    """Missing, another tenant's, or another user's private dashboard (Section 7.2)."""

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


class ValidationFailedError(DomainError):
    code = "VALIDATION_FAILED"
    status_code = 422
    message = "The request payload is invalid."


class ChartSpecInvalidError(DomainError):
    """visualization-service rejected the spec or the overrides (Section 17)."""

    code = "CHART_SPEC_INVALID"
    status_code = 422
    message = "The chart specification is not valid for this result."


class DashboardNotOwnerError(DomainError):
    code = "DASHBOARD_NOT_OWNER"
    status_code = 403
    message = "Only the dashboard's owner can change it."


class ArtifactConflictError(DomainError):
    code = "ARTIFACT_CONFLICT"
    status_code = 409
    message = "An artifact with this id already exists for a different run."


class ArtifactWriterNotAllowedError(DomainError):
    code = "ARTIFACT_WRITER_NOT_ALLOWED"
    status_code = 403
    message = "This caller may not store artifacts."


class ArtifactResultExpiredError(DomainError):
    code = "ARTIFACT_RESULT_EXPIRED"
    status_code = 410
    message = "This artifact's result has expired."


class InvalidCursorError(DomainError):
    code = "INVALID_CURSOR"
    status_code = 422
    message = "The pagination cursor is invalid."


class UpstreamUnavailableError(DomainError):
    code = "UPSTREAM_UNAVAILABLE"
    status_code = 502
    message = "A backing service is unavailable."


class UpstreamTimeoutError(DomainError):
    code = "UPSTREAM_TIMEOUT"
    status_code = 504
    message = "A backing service did not respond in time."


class ShareLinkLimitError(DomainError):
    code = "SHARE_LINK_LIMIT"
    status_code = 409
    message = "This dashboard has too many active share links. Revoke one first."
