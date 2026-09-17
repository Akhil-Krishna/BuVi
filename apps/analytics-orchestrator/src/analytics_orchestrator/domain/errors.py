"""API errors (Section 21). A run's own failures are `FailureCode`s on the run, not these."""

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


class ValidationFailedError(DomainError):
    code = "VALIDATION_FAILED"
    status_code = 422
    message = "The request payload is invalid."


class UpstreamUnavailableError(DomainError):
    code = "UPSTREAM_UNAVAILABLE"
    status_code = 502
    message = "A backing service is unavailable."


class UpstreamTimeoutError(DomainError):
    code = "UPSTREAM_TIMEOUT"
    status_code = 504
    message = "A backing service did not respond in time."


class IdempotencyKeyReusedError(DomainError):
    code = "IDEMPOTENCY_KEY_REUSED"
    status_code = 409
    message = "This Idempotency-Key was already used for a different request."


class RunBusyError(DomainError):
    code = "RUN_BUSY"
    status_code = 409
    message = "This run is already being executed."


class RunNotCancellableError(DomainError):
    code = "RUN_NOT_CANCELLABLE"
    status_code = 409
    message = "This run has already finished."


class QueueUnavailableError(DomainError):
    code = "QUEUE_UNAVAILABLE"
    status_code = 503
    message = "The run could not be queued. Retry later."
