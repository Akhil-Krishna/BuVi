"""A software WebAuthn authenticator (Phase A10), so tests drive the real ceremonies without a
browser: `none` attestation, one ES256 (P-256) key, and a signature counter.

It builds exactly what a browser would send (`navigator.credentials.create/get` responses as
JSON), so identity-service's verification by py_webauthn runs unmodified. Knobs make it
misbehave on purpose: a wrong origin, a replayed or regressed counter (a cloned key).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

_UP, _UV, _AT = 0x01, 0x04, 0x40


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class SoftAuthenticator:
    def __init__(self, origin: str = "http://localhost:3000") -> None:
        self.origin = origin
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.sign_count = 0

    def _cose_key(self) -> bytes:
        numbers = self.key.public_key().public_numbers()
        return cbor2.dumps(
            {
                1: 2,  # kty: EC2
                3: -7,  # alg: ES256
                -1: 1,  # crv: P-256
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )

    def _client_data(self, kind: str, challenge: str, origin: str | None) -> bytes:
        return json.dumps(
            {
                "type": kind,
                "challenge": challenge,
                "origin": origin or self.origin,
                "crossOrigin": False,
            },
            separators=(",", ":"),
        ).encode()

    def register(self, options: dict[str, Any], *, origin: str | None = None) -> dict[str, Any]:
        """The response to `navigator.credentials.create({publicKey: options})`."""
        client_data = self._client_data("webauthn.create", options["challenge"], origin)
        attested = (
            bytes(16)  # AAGUID
            + len(self.credential_id).to_bytes(2, "big")
            + self.credential_id
            + self._cose_key()
        )
        auth_data = (
            hashlib.sha256(options["rp"]["id"].encode()).digest()
            + bytes([_UP | _UV | _AT])
            + self.sign_count.to_bytes(4, "big")
            + attested
        )
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "attestationObject": b64url(attestation),
                "transports": ["usb"],
            },
            "clientExtensionResults": {},
        }

    def assert_(
        self,
        options: dict[str, Any],
        *,
        origin: str | None = None,
        sign_count: int | None = None,
    ) -> dict[str, Any]:
        """The response to `navigator.credentials.get({publicKey: options})`. By default the
        counter advances; pass `sign_count` to replay or regress it (a cloned key)."""
        if sign_count is None:
            self.sign_count += 1
            sign_count = self.sign_count
        client_data = self._client_data("webauthn.get", options["challenge"], origin)
        auth_data = (
            hashlib.sha256(options["rpId"].encode()).digest()
            + bytes([_UP | _UV])
            + sign_count.to_bytes(4, "big")
        )
        signature = self.key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(auth_data),
                "signature": b64url(signature),
            },
            "clientExtensionResults": {},
        }
