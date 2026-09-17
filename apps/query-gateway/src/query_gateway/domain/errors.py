"""Domain errors and their stable API error codes (Section 21).

Never in an error: SQL-parser internals, the SQL text, a driver message, a host, a user, a
credential, or a Vault path. `QUERY_VALIDATION_FAILED` carries only the rejection reason and
the offending operation, function or identifier as the caller wrote it.
"""

from __future__ import annotations

from platform_observability.errors import ApiError


class DomainError(ApiError):
    code: str = "INTERNAL_ERROR"
    status_code: int = 500
    message: str = "An unexpected error occurred."


class ValidationFailedError(DomainError):
    code = "VALIDATION_FAILED"
    status_code = 422
    message = "The request payload is invalid."


class NotFoundError(DomainError):
    """The data source does not exist, or belongs to another tenant (Section 7.2)."""

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
    message = "Insufficient permissions."


class PurposeNotAllowedError(DomainError):
    """The calling service may not run queries for this purpose (ADR 0005)."""

    code = "PURPOSE_NOT_ALLOWED"
    status_code = 403
    message = "This caller may not run queries for that purpose."


class PurposeNotSupportedError(DomainError):
    code = "PURPOSE_NOT_SUPPORTED"
    status_code = 422
    message = "This query purpose is not supported yet."


class DelegationNotAllowedError(DomainError):
    """`on_behalf_of` from a caller, purpose or request shape that may not delegate (Section 13)."""

    code = "DELEGATION_NOT_ALLOWED"
    status_code = 403
    message = "This caller may not act on behalf of a user for this request."


class DataSourceNotActiveError(DomainError):
    code = "DATA_SOURCE_NOT_ACTIVE"
    status_code = 409
    message = "The data source is not active. Test and sync it first."


class EngineNotSupportedError(DomainError):
    code = "ENGINE_NOT_SUPPORTED"
    status_code = 422
    message = "This database engine is not supported yet."


class QueryValidationFailedError(DomainError):
    """Section 13: the SQL did not pass the allow-list. The Section 21 example code."""

    code = "QUERY_VALIDATION_FAILED"
    status_code = 422
    message = "The query uses a blocked operation."


class QueryExecutionFailedError(DomainError):
    code = "QUERY_EXECUTION_FAILED"
    status_code = 422
    message = "The database could not execute the query."


class QueryTimeoutError(DomainError):
    code = "QUERY_TIMEOUT"
    status_code = 504
    message = "The query exceeded its time limit."


class DataSourceUnavailableError(DomainError):
    code = "DATA_SOURCE_UNAVAILABLE"
    status_code = 502
    message = "The data source could not be reached."


class QueryConcurrencyLimitedError(DomainError):
    """Section 20: per-tenant concurrency cap."""

    code = "QUERY_CONCURRENCY_LIMITED"
    status_code = 429
    message = "Too many queries are running for this organization. Retry shortly."


class UpstreamUnavailableError(DomainError):
    code = "UPSTREAM_UNAVAILABLE"
    status_code = 502
    message = "A backing service is unavailable."


class UpstreamTimeoutError(DomainError):
    code = "UPSTREAM_TIMEOUT"
    status_code = 504
    message = "A backing service did not respond in time."


class SecretStoreUnavailableError(DomainError):
    code = "SECRET_STORE_UNAVAILABLE"
    status_code = 503
    message = "The secret store is unavailable. Retry later."


class ResultExpiredError(DomainError):
    """The result handle's TTL has passed; the rows are gone (Section 13). Never re-executed."""

    code = "RESULT_EXPIRED"
    status_code = 410
    message = "This result has expired."


class ResultReaderNotAllowedError(DomainError):
    code = "RESULT_READER_NOT_ALLOWED"
    status_code = 403
    message = "This caller may not read stored results."


class ResultStoreUnavailableError(DomainError):
    code = "RESULT_STORE_UNAVAILABLE"
    status_code = 503
    message = "The result store is unavailable. Retry later."
