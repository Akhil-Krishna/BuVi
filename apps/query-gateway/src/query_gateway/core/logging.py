"""Logging for query-gateway (Sections 21, 22, 24).

Two service-specific rules on top of `platform_observability`:

* sqlglot warns with the offending SQL text ("... contains unsupported syntax"). SQL text is
  recorded once, in `query_executions.sql_text`, not scattered through logs, so its logger is
  held at ERROR.
* Driver errors are logged by type and code only -- a message can name host, port and user.
"""

from __future__ import annotations

import logging

from platform_observability.logging import configure_logging as _configure_logging


def configure_logging(level: str = "INFO") -> None:
    _configure_logging("query-gateway", level)
    logging.getLogger("sqlglot").setLevel(logging.ERROR)
