"""Sanitized connectivity diagnostics (Section 13.1).

"returns only status + sanitized diagnostics (e.g., 'Connected. 42 tables
discovered.' not the DSN or raw driver error text, which can leak host/port/user)."

Every outcome is one stable code with one fixed message. A driver's own text never
reaches a response, a log line, or a database row.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class DiagnosticCode(StrEnum):
    CONNECTED = "CONNECTED"
    SYNCED = "SYNCED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    DATABASE_NOT_FOUND = "DATABASE_NOT_FOUND"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    HOST_UNREACHABLE = "HOST_UNREACHABLE"
    DESTINATION_NOT_ALLOWED = "DESTINATION_NOT_ALLOWED"
    TLS_ERROR = "TLS_ERROR"
    TIMEOUT = "TIMEOUT"
    TOO_MANY_CONNECTIONS = "TOO_MANY_CONNECTIONS"
    CATALOG_TOO_LARGE = "CATALOG_TOO_LARGE"
    CONNECTION_FAILED = "CONNECTION_FAILED"


FAILURE_MESSAGES: Final[dict[DiagnosticCode, str]] = {
    DiagnosticCode.AUTHENTICATION_FAILED: "The database rejected the credentials.",
    DiagnosticCode.DATABASE_NOT_FOUND: "The database does not exist or cannot be accessed.",
    DiagnosticCode.PERMISSION_DENIED: (
        "The connected role lacks the privileges needed to read the catalog."
    ),
    DiagnosticCode.HOST_UNREACHABLE: "The database host could not be reached.",
    DiagnosticCode.DESTINATION_NOT_ALLOWED: (
        "This destination is not allowed for data-source connections."
    ),
    DiagnosticCode.TLS_ERROR: (
        "A secure connection could not be established with the requested SSL mode."
    ),
    DiagnosticCode.TIMEOUT: "The database did not respond in time.",
    DiagnosticCode.TOO_MANY_CONNECTIONS: (
        "The database refused the connection: too many connections."
    ),
    DiagnosticCode.CATALOG_TOO_LARGE: "The catalog exceeds the sync limits for one data source.",
    DiagnosticCode.CONNECTION_FAILED: "The connection failed.",
}


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def connected_message(tables: int) -> str:
    return f"Connected. {_plural(tables, 'table')} discovered."


def synced_message(tables: int, columns: int) -> str:
    return f"Catalog synced: {_plural(tables, 'table')}, {_plural(columns, 'column')}."


def failure_message(code: DiagnosticCode) -> str:
    return FAILURE_MESSAGES.get(code, FAILURE_MESSAGES[DiagnosticCode.CONNECTION_FAILED])


@dataclass(frozen=True)
class ConnectivityResult:
    ok: bool
    code: DiagnosticCode
    message: str
    latency_ms: int
    tables_discovered: int | None = None


class ConnectorError(Exception):
    """A connector failed. Carries a diagnostic code and nothing else."""

    def __init__(self, code: DiagnosticCode) -> None:
        super().__init__(code.value)
        self.code = code
