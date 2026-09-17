"""Section 9 Idempotency-Key rules as pure functions: key syntax, request fingerprint, record
scope, and which outcomes are final enough to record."""

from __future__ import annotations

import hashlib
import re
from typing import Final

_KEY: Final = re.compile(r"^[\x21-\x7e]{1,255}$")
#: Not final: the client may retry these (credentials, permissions, contention, limits, 5xx).
RETRYABLE: Final = frozenset({401, 403, 408, 409, 425, 429})


def valid_key(key: str) -> bool:
    return bool(_KEY.match(key))


def record_key(tenant_id: str, principal_id: str, key: str) -> str:
    """Scope: tenant + principal + key. The key is hashed: bounded size, no client text in Redis."""
    digest = hashlib.sha256(key.encode("ascii")).hexdigest()
    return f"idem:{tenant_id}:{principal_id}:{digest}"


def fingerprint(method: str, path: str, query: str, body: bytes) -> str:
    """Route is part of the fingerprint, so one key reused on another route is detected."""
    digest = hashlib.sha256()
    for part in (method.upper().encode(), path.encode(), query.encode()):
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    digest.update(body)
    return digest.hexdigest()


def is_final(status_code: int) -> bool:
    return status_code < 500 and status_code not in RETRYABLE
