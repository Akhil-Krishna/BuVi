"""Webhook signatures (Phase A11). Pure.

`X-Buvi-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>." + body)>`. The timestamp
is inside the signed bytes, so a receiver can refuse replays older than its tolerance; the body
is signed exactly as sent.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Final

SIGNATURE_HEADER: Final = "X-Buvi-Signature"
SECRET_PREFIX: Final = "whsec_"  # noqa: S105 - a prefix, not a secret


def new_signing_secret() -> str:
    return f"{SECRET_PREFIX}{secrets.token_urlsafe(32)}"


def sign(secret: str, timestamp: int, body: bytes) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"t={timestamp},v1={digest.hexdigest()}"


def verify(secret: str, header: str, body: bytes) -> bool:
    """What a receiver does; used by tests and the live flow."""
    try:
        parts = dict(item.split("=", 1) for item in header.split(","))
        expected = sign(secret, int(parts["t"]), body)
    except (KeyError, ValueError):
        return False
    return hmac.compare_digest(expected, header)
