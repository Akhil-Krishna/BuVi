"""OpenTelemetry bootstrap, logging config, correlation and the error envelope (Sections 21, 22).

Contract: `configure_logging`, `redact`, `request_id_var`, `RequestIdMiddleware`,
`ApiError`, `error_response`, `install_error_handlers`, `docs_routes`. The SSRF-safe outbound fetch
client (Section 15/33) joins this package when the first user-directed fetch lands.
"""

from platform_observability.app import docs_routes
from platform_observability.correlation import (
    REQUEST_ID_HEADER,
    RequestIdMiddleware,
    new_request_id,
)
from platform_observability.errors import ApiError, error_response, install_error_handlers
from platform_observability.logging import REDACTED, configure_logging, redact, request_id_var

__all__ = [
    "REDACTED",
    "REQUEST_ID_HEADER",
    "ApiError",
    "RequestIdMiddleware",
    "configure_logging",
    "docs_routes",
    "error_response",
    "install_error_handlers",
    "new_request_id",
    "redact",
    "request_id_var",
]
