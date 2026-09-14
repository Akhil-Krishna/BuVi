"""PKCE parameters for the OIDC Authorization Code flow (Section 6.1, RFC 7636).

The verifier is generated server-side and never reaches the browser: it lives in
a short-lived HttpOnly cookie between `/auth/login` and `/auth/callback`, so an
intercepted authorization code cannot be redeemed by anyone else.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Final

#: RFC 7636 section 4.1 permits 43-128 characters; 96 bytes yields 128.
VERIFIER_ENTROPY_BYTES: Final = 96
CHALLENGE_METHOD: Final = "S256"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@dataclass(frozen=True)
class PkceChallenge:
    """A verifier/challenge pair plus the CSRF `state` and replay `nonce`."""

    verifier: str
    challenge: str
    state: str
    nonce: str
    method: str = CHALLENGE_METHOD


def create_pkce_challenge() -> PkceChallenge:
    """Generate a fresh PKCE challenge for one authorization request."""
    verifier = _b64url(secrets.token_bytes(VERIFIER_ENTROPY_BYTES))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return PkceChallenge(
        verifier=verifier,
        challenge=challenge,
        state=secrets.token_urlsafe(32),
        nonce=secrets.token_urlsafe(32),
    )
