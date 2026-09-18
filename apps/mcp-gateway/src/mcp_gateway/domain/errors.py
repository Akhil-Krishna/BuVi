"""API errors (Section 21). No message ever carries an MCP server's own text."""

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


class EndpointInvalidError(DomainError):
    """The endpoint URL breaks a Section 15 rule; `details.reason` says which."""

    code = "MCP_ENDPOINT_INVALID"
    status_code = 422
    message = "The MCP endpoint URL is not allowed."


class ManifestInvalidError(DomainError):
    code = "MCP_MANIFEST_INVALID"
    status_code = 422
    message = "The declared tool manifest is not valid."


class ManifestMismatchError(DomainError):
    """Approval found the live server disagreeing with the declared manifest (Section 14)."""

    code = "MCP_MANIFEST_MISMATCH"
    status_code = 422
    message = "The server's tools do not match the declared manifest."


class InvalidTransitionError(DomainError):
    code = "INVALID_STATUS_TRANSITION"
    status_code = 409
    message = "The server cannot move to that status from its current one."


class ToolNotFoundError(DomainError):
    code = "MCP_TOOL_NOT_FOUND"
    status_code = 404
    message = "No such tool is declared on this server."


class GrantNotFoundError(DomainError):
    code = "NOT_FOUND"
    status_code = 404
    message = "Not found."


class ToolNotGrantableError(DomainError):
    code = "MCP_TOOL_NOT_GRANTABLE"
    status_code = 409
    message = "Write and admin tools cannot be granted until step-up confirmation exists."


class GrantExistsError(DomainError):
    code = "MCP_GRANT_EXISTS"
    status_code = 409
    message = "This grant already exists."


class GrantInvalidError(DomainError):
    code = "MCP_GRANT_INVALID"
    status_code = 422
    message = "A grant names exactly one tenant role or one user."


class ArgumentsTooLargeError(DomainError):
    code = "MCP_ARGUMENTS_TOO_LARGE"
    status_code = 422
    message = "The tool arguments are too large."


class InvocationDeniedError(DomainError):
    """An invocation refused by policy; the attempt is recorded before this is raised."""

    status_code = 403

    def __init__(self, reason: str) -> None:
        super().__init__(DENIAL_MESSAGES.get(reason, "This invocation is not allowed."))
        self.code = DENIAL_CODES.get(reason, "MCP_INVOCATION_DENIED")


DENIAL_CODES: dict[str, str] = {
    "server_not_approved": "MCP_SERVER_NOT_APPROVED",
    "tool_denied_by_policy": "MCP_TOOL_DENIED",
    "not_granted": "MCP_TOOL_NOT_GRANTED",
    "destination_not_allowed": "DESTINATION_NOT_ALLOWED",
}
DENIAL_MESSAGES: dict[str, str] = {
    "server_not_approved": "This MCP server is not approved.",
    "tool_denied_by_policy": "This tool may not be invoked.",
    "not_granted": "You have not been granted this tool.",
    "destination_not_allowed": "The server's address is not an allowed destination.",
}


class UpstreamError(DomainError):
    """The MCP server failed; `details.reason` is a fixed code, never the server's text."""

    code = "MCP_UPSTREAM_ERROR"
    status_code = 502
    message = "The MCP server did not complete the request."


class SecretStoreUnavailableError(DomainError):
    code = "SECRET_STORE_UNAVAILABLE"
    status_code = 503
    message = "The secret store is unavailable."


class UpstreamUnavailableError(DomainError):
    code = "UPSTREAM_UNAVAILABLE"
    status_code = 502
    message = "A backing service is unavailable."


class UpstreamTimeoutError(DomainError):
    code = "UPSTREAM_TIMEOUT"
    status_code = 504
    message = "A backing service did not respond in time."
