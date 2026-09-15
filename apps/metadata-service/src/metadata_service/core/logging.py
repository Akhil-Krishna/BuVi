"""Logging for metadata-service (Sections 22, 24).

JSON formatting, correlation and redaction are shared platform behaviour in
`platform_observability`. One rule specific to this service: a database driver's
exception message can contain the host, port and user of a customer database
(Section 13.1), so connector failures are logged by error *type* and diagnostic
code only -- never `str(exc)`, never a traceback.
"""

from __future__ import annotations

from platform_observability.logging import configure_logging as _configure_logging


def configure_logging(level: str = "INFO") -> None:
    _configure_logging("metadata-service", level)
