"""Opaque token generation and hashing (Sections 6.7, 6.8, 6.9).

Three token families exist in this service, none of them reversible at rest:

* **session ids** -- random, stored hashed; the raw value only ever lives in the
  browser's HttpOnly cookie.
* **invitation tokens** -- random, single-use, stored hashed with a 7-day expiry.
* **API keys** -- random, shown exactly once, stored as an argon2id hash.

A stolen database dump therefore yields no usable credential (Section 24).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Final

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

#: 256 bits of entropy. `token_urlsafe(32)` produces 43 URL-safe characters.
TOKEN_ENTROPY_BYTES: Final = 32

#: Number of random characters kept alongside the environment marker in
#: `identity.api_keys.key_prefix`, so two keys are distinguishable in a list.
API_KEY_PREFIX_RANDOM_CHARS: Final = 6

_hasher = PasswordHasher()


def generate_token() -> str:
    """A new opaque, URL-safe, cryptographically random token."""
    return secrets.token_urlsafe(TOKEN_ENTROPY_BYTES)


def hash_token(raw: str) -> str:
    """Hash a high-entropy token for storage.

    SHA-256 is correct here and argon2 is not: these tokens are 256-bit random
    values, so there is no dictionary to slow an attacker down against, and
    session lookup happens on every single request. Argon2id is reserved for
    values a human chose (`verify_secret` below).
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def tokens_match(raw: str, stored_hash: str) -> bool:
    """Constant-time comparison of a presented token against its stored hash."""
    return hmac.compare_digest(hash_token(raw), stored_hash)


def hash_secret(raw: str) -> str:
    """Argon2id hash, for API keys and local passwords (Sections 6.5, 6.8)."""
    return _hasher.hash(raw)


def verify_secret(raw: str, stored_hash: str) -> bool:
    """Verify an argon2id hash without leaking timing or raising on mismatch."""
    try:
        return _hasher.verify(stored_hash, raw)
    except (VerifyMismatchError, ValueError):
        return False


def generate_api_key(prefix: str) -> tuple[str, str]:
    """Mint an API key.

    Returns `(full_key, key_prefix)`. The full key is shown to the caller once
    and never stored; `key_prefix` is the non-secret display value persisted in
    `identity.api_keys.key_prefix`.
    """
    random_part = secrets.token_urlsafe(TOKEN_ENTROPY_BYTES)
    full_key = f"{prefix}{random_part}"
    key_prefix = f"{prefix}{random_part[:API_KEY_PREFIX_RANDOM_CHARS]}"
    return full_key, key_prefix
