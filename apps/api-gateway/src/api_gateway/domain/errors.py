"""Gateway errors, mapped onto the shared Section 21 envelope."""

from __future__ import annotations

from platform_observability.errors import ApiError


class AuthenticationRequiredError(ApiError):
    code = "AUTHENTICATION_REQUIRED"
    status_code = 401
    message = "Authentication required."


class UserNotActiveError(ApiError):
    code = "USER_NOT_ACTIVE"
    status_code = 403
    message = "This account is not active."


class ForbiddenError(ApiError):
    code = "FORBIDDEN"
    status_code = 403
    message = "Insufficient permissions."


class NotFoundError(ApiError):
    code = "NOT_FOUND"
    status_code = 404
    message = "Not found."


class RateLimitedError(ApiError):
    code = "RATE_LIMITED"
    status_code = 429
    message = "Too many requests. Retry later."


class PayloadTooLargeError(ApiError):
    code = "PAYLOAD_TOO_LARGE"
    status_code = 413
    message = "The request body is too large."


class IdempotencyKeyInvalidError(ApiError):
    code = "VALIDATION_FAILED"
    status_code = 422
    message = "The Idempotency-Key header must be 1-255 printable ASCII characters."


class IdempotencyKeyReusedError(ApiError):
    code = "IDEMPOTENCY_KEY_REUSED"
    status_code = 409
    message = "This Idempotency-Key was already used for a different request."


class IdempotencyInProgressError(ApiError):
    code = "IDEMPOTENCY_REQUEST_IN_PROGRESS"
    status_code = 409
    message = "A request with this Idempotency-Key is still being processed."


class IdempotentReplayUnavailableError(ApiError):
    code = "IDEMPOTENT_REPLAY_UNAVAILABLE"
    status_code = 409
    message = "This request already completed; its response cannot be shown again."


class IdempotencyUnavailableError(ApiError):
    code = "IDEMPOTENCY_UNAVAILABLE"
    status_code = 503
    message = "Idempotent requests cannot be processed right now. Retry later."


class NotImplementedYetError(ApiError):
    code = "NOT_IMPLEMENTED"
    status_code = 501
    message = "This endpoint's backing service is not available yet."


class UpstreamUnavailableError(ApiError):
    code = "UPSTREAM_UNAVAILABLE"
    status_code = 502
    message = "A backing service is unavailable."


class UpstreamTimeoutError(ApiError):
    code = "UPSTREAM_TIMEOUT"
    status_code = 504
    message = "A backing service did not respond in time."
