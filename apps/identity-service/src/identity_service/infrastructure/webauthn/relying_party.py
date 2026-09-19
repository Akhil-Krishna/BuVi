"""The WebAuthn relying party (Section 6.6; Phase A10): a thin adapter over py_webauthn.

The library does the ceremony checks: challenge, origin, RP ID hash, user presence, signature,
and signature-counter regression. This module only fixes our choices and translates failures
into one exception:

* attestation `none`: we store the key, we do not vet authenticator models;
* ES256, EdDSA and RS256 keys;
* user verification `preferred`. Presence is always required; a PIN or biometric is not,
  so plain security keys work as a second factor.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)


class CeremonyFailedError(Exception):
    """The browser's response did not verify. The reason is not exposed to callers."""


@dataclass(frozen=True)
class RegisteredKey:
    credential_id: bytes
    public_key: bytes
    sign_count: int


@dataclass(frozen=True)
class Options:
    """Options for the browser (`navigator.credentials.create/get`), and the challenge."""

    public_key: dict[str, Any]
    challenge: bytes


class RelyingParty:
    def __init__(
        self, *, rp_id: str, rp_name: str, origins: list[str], timeout_seconds: int
    ) -> None:
        self._rp_id = rp_id
        self._rp_name = rp_name
        self._origins = origins
        self._timeout_ms = timeout_seconds * 1000

    def registration_options(
        self, *, user_id: bytes, user_name: str, display_name: str, exclude: list[bytes]
    ) -> Options:
        options = generate_registration_options(
            rp_id=self._rp_id,
            rp_name=self._rp_name,
            user_id=user_id,
            user_name=user_name,
            user_display_name=display_name,
            timeout=self._timeout_ms,
            attestation=AttestationConveyancePreference.NONE,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.DISCOURAGED,
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
            exclude_credentials=[PublicKeyCredentialDescriptor(id=c) for c in exclude],
        )
        return Options(json.loads(options_to_json(options)), options.challenge)

    def authentication_options(self, *, allow: list[bytes]) -> Options:
        options = generate_authentication_options(
            rp_id=self._rp_id,
            timeout=self._timeout_ms,
            allow_credentials=[PublicKeyCredentialDescriptor(id=c) for c in allow],
            user_verification=UserVerificationRequirement.PREFERRED,
        )
        return Options(json.loads(options_to_json(options)), options.challenge)

    def verify_registration(self, credential: dict[str, Any], challenge: bytes) -> RegisteredKey:
        try:
            verified = verify_registration_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=self._rp_id,
                expected_origin=self._origins,
            )
        except (WebAuthnException, KeyError, TypeError, ValueError):
            raise CeremonyFailedError() from None
        return RegisteredKey(
            verified.credential_id, verified.credential_public_key, verified.sign_count
        )

    def verify_assertion(
        self, credential: dict[str, Any], challenge: bytes, *, public_key: bytes, sign_count: int
    ) -> int:
        """The authenticator's new signature counter (a cloned key shows a regression)."""
        try:
            verified = verify_authentication_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=self._rp_id,
                expected_origin=self._origins,
                credential_public_key=public_key,
                credential_current_sign_count=sign_count,
            )
        except (WebAuthnException, KeyError, TypeError, ValueError):
            raise CeremonyFailedError() from None
        return int(verified.new_sign_count)


def credential_id_of(credential: dict[str, Any]) -> str | None:
    """The base64url credential id a browser response names, if well formed."""
    raw = credential.get("rawId") or credential.get("id")
    return raw if isinstance(raw, str) and 0 < len(raw) <= 1024 else None


__all__ = [
    "CeremonyFailedError",
    "Options",
    "RegisteredKey",
    "RelyingParty",
    "bytes_to_base64url",
    "credential_id_of",
]
