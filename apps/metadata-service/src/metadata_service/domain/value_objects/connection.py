"""Connection value objects (Sections 8.2, 13.1).

A data source's *non-secret* description lives in `metadata.data_sources`; its
credentials -- host, port, user, password, SSL mode -- live only in Vault. Section
13.1 treats the host, port and user as sensitive too ("raw driver error text ... can
leak host/port/user"), so all five are in the secret and none is in a table, a
response, or a log.
"""

from __future__ import annotations

import ipaddress
import re
import uuid
from dataclasses import dataclass, field
from typing import Final, Literal, get_args

#: Section 8.2 `engine` CHECK constraint.
ENGINES: Final[tuple[str, ...]] = ("postgres", "mysql", "snowflake", "bigquery", "redshift")
#: Engines with a connector. Phase A3 ships one Postgres connector; Phase A8 adds more.
SUPPORTED_ENGINES: Final[frozenset[str]] = frozenset({"postgres"})

#: Section 8.2 `status` CHECK constraint.
STATUS_PENDING: Final = "pending"
STATUS_ACTIVE: Final = "active"
STATUS_ERROR: Final = "error"
STATUS_DISABLED: Final = "disabled"

SslMode = Literal["disable", "require", "verify-full"]
SSL_MODES: Final[tuple[str, ...]] = get_args(SslMode)

MAX_ALLOWED_SCHEMAS: Final = 100

_SCHEMA_NAME: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")
_DATABASE_NAME: Final = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_$.\-]{0,62}$")
_HOST_LABEL_FORBIDDEN: Final = re.compile(r"://|@|=|passw|pwd", re.IGNORECASE)
_HOSTNAME_LABEL: Final = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?$")
_USERNAME: Final = re.compile(r"^[^\x00-\x1f\x7f]{1,63}$")


def validate_schema_name(name: str) -> str:
    """A plain Postgres identifier; quoting tricks and system schemas are refused."""
    if not _SCHEMA_NAME.match(name) or name.lower().startswith("pg_"):
        raise ValueError("invalid schema name")
    if name.lower() == "information_schema":
        raise ValueError("invalid schema name")
    return name


def validate_database_name(name: str) -> str:
    if not _DATABASE_NAME.match(name):
        raise ValueError("invalid database name")
    return name


def validate_host_label(label: str) -> str:
    """A display label only (Section 8.2: "never full DSN").

    Anything shaped like a connection string -- a scheme, `user@host`, `key=value`
    pairs, or a password keyword -- is refused, so a DSN pasted into the wrong field
    never lands in a table that every catalog read returns.
    """
    value = label.strip()
    if not 1 <= len(value) <= 200 or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError("invalid host label")
    if _HOST_LABEL_FORBIDDEN.search(value):
        raise ValueError("host_label must be a display label, not a connection string")
    return value


def validate_host(host: str) -> str:
    """An IP literal or an RFC 1123 hostname -- nothing that could carry a path or userinfo."""
    value = host.strip().rstrip(".")
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    if not 1 <= len(value) <= 253 or not all(_HOSTNAME_LABEL.match(p) for p in value.split(".")):
        raise ValueError("invalid host")
    return value.lower()


def validate_username(username: str) -> str:
    if not _USERNAME.match(username):
        raise ValueError("invalid username")
    return username


def data_source_secret_ref(tenant_id: uuid.UUID, data_source_id: uuid.UUID) -> str:
    """The KV reference of a data source's credentials (Section 8.2 `secret_ref` layout)."""
    return f"tenants/{tenant_id}/datasources/{data_source_id}"


@dataclass(frozen=True)
class ConnectionSecret:
    """Credentials for one data source. Exists only in memory and in Vault.

    Every field is excluded from `repr`, so an accidental `logger.info(secret)` or an
    exception that captures it prints nothing sensitive.
    """

    host: str = field(repr=False)
    port: int = field(repr=False)
    username: str = field(repr=False)
    password: str = field(repr=False)
    sslmode: SslMode = field(repr=False)

    def __post_init__(self) -> None:
        validate_host(self.host)
        validate_username(self.username)
        if not 1 <= self.port <= 65535:
            raise ValueError("invalid port")
        if not self.password or len(self.password) > 1024:
            raise ValueError("invalid password")
        if self.sslmode not in SSL_MODES:
            raise ValueError("invalid sslmode")

    def to_secret_payload(self) -> dict[str, str]:
        return {
            "host": self.host,
            "port": str(self.port),
            "username": self.username,
            "password": self.password,
            "sslmode": self.sslmode,
        }

    @classmethod
    def from_secret_payload(cls, payload: dict[str, str]) -> ConnectionSecret:
        """Rebuild from a Vault payload. Raises `ValueError` without echoing any value."""
        try:
            sslmode = payload["sslmode"]
            if sslmode not in SSL_MODES:
                raise ValueError("invalid sslmode")
            return cls(
                host=payload["host"],
                port=int(payload["port"]),
                username=payload["username"],
                password=payload["password"],
                sslmode=sslmode,  # type: ignore[arg-type]
            )
        except (KeyError, TypeError, ValueError):
            raise ValueError("malformed connection secret") from None


@dataclass(frozen=True)
class ConnectionTarget:
    """Everything a connector needs to reach one data source."""

    engine: str
    database_name: str
    allowed_schemas: tuple[str, ...]
    secret: ConnectionSecret = field(repr=False)
