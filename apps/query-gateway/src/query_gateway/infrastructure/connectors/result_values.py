"""What every executor shares: turning driver values into JSON-safe result cells, and keying
connection pools by credential so a rotated credential gets a fresh pool."""

from __future__ import annotations

import base64
import datetime as dt
import decimal
import hashlib
import uuid
from typing import Any

from query_gateway.domain.value_objects.execution import ConnectionCredentials


def credential_fingerprint(credentials: ConnectionCredentials) -> str:
    raw = "\x1f".join(
        [
            credentials.host,
            str(credentials.port),
            credentials.username,
            credentials.password,
            credentials.sslmode,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def json_value(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, list | tuple):
        return [json_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    return str(value)
