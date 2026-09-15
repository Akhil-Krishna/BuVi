"""Execution inputs and outputs (Section 13): credentials, limits, the capped result, failures.

Pure data. No field of `ConnectionCredentials` is printable, and `ExecutionError` carries a
code and a SQLSTATE class -- never a driver message.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

SslMode = Literal["disable", "require", "verify-full"]
_SSL_MODES = ("disable", "require", "verify-full")


@dataclass(frozen=True)
class ConnectionCredentials:
    host: str = field(repr=False)
    port: int = field(repr=False)
    username: str = field(repr=False)
    password: str = field(repr=False)
    sslmode: SslMode = field(repr=False)

    @classmethod
    def from_secret_payload(cls, payload: dict[str, str]) -> ConnectionCredentials:
        """Rebuild from the Vault payload metadata-service wrote. No value is echoed on error."""
        try:
            sslmode = payload["sslmode"]
            port = int(payload["port"])
            if sslmode not in _SSL_MODES or not 1 <= port <= 65535 or not payload["password"]:
                raise ValueError("invalid")
            return cls(
                host=payload["host"],
                port=port,
                username=payload["username"],
                password=payload["password"],
                sslmode=sslmode,  # type: ignore[arg-type]
            )
        except (KeyError, TypeError, ValueError):
            raise ValueError("malformed connection secret") from None


@dataclass(frozen=True)
class ExecutionLimits:
    max_rows: int
    max_bytes: int
    timeout_ms: int


@dataclass(frozen=True)
class ResultColumn:
    name: str
    type: str


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[ResultColumn, ...]
    rows: list[list[Any]]
    truncated: bool
    #: "rows" or "bytes" when truncated -- rows are never dropped without this flag (Section 13).
    truncation_reason: str | None
    bytes_returned: int

    @property
    def row_count(self) -> int:
        return len(self.rows)


class ExecutionFailure(StrEnum):
    TIMEOUT = "QUERY_TIMEOUT"
    DESTINATION_NOT_ALLOWED = "DESTINATION_NOT_ALLOWED"
    UNAVAILABLE = "DATA_SOURCE_UNAVAILABLE"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    REJECTED_BY_DATABASE = "REJECTED_BY_DATABASE"
    QUERY_FAILED = "QUERY_FAILED"


class ExecutionError(Exception):
    def __init__(self, failure: ExecutionFailure, sqlstate_class: str | None = None) -> None:
        super().__init__(failure.value)
        self.failure = failure
        self.sqlstate_class = sqlstate_class
