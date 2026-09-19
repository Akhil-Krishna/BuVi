"""The authenticated caller, as every service sees it (Section 7.2).

`Principal` is the only representation of "who is calling" that crosses a
service boundary. It is deliberately small and frozen: a request handler cannot
widen its own authority by mutating it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal

AuthMethod = Literal["session", "api_key", "service_jwt"]
MfaMethod = Literal["totp", "webauthn"]

#: Section 7.3: a step-up operation needs an MFA verification no older than this.
STEP_UP_MAX_AGE: Final = timedelta(minutes=5)


@dataclass(frozen=True)
class Principal:
    """The caller's identity, tenancy, and resolved permissions.

    Matches the Section 7.2 contract. Two fields extend it, per the Section 0
    rule that a given contract may be extended but not renamed:

    `mfa_verified_at`
        Section 7.3 requires a *recent* (<= 5 min) MFA verification for
        sensitive operations. `mfa_verified` alone records only that MFA
        happened at some point, which cannot answer "recently". Recency lives
        here so `require_step_up` can be a pure function of the principal.

    `roles`
        Carried for audit-log attribution (Section 22) and for the
        `platform_super_admin` checks in Section 2. Authorization decisions read
        `permissions`, never `roles`.

    `mfa_method`, `webauthn_required` (Phase A10)
        How the last MFA check was made, and whether this caller's step-up must be
        WebAuthn. identity-service decides the requirement (tenant policy for
        `org_admin`); `platform_super_admin` always needs it (Section 6.6), whatever
        the payload says. A step-up made with the wrong method is not fresh.
    """

    user_id: str
    tenant_id: str
    permissions: frozenset[str]
    auth_method: AuthMethod
    mfa_verified: bool
    session_id: str | None = None
    mfa_verified_at: datetime | None = None
    roles: frozenset[str] = field(default_factory=frozenset)
    mfa_method: MfaMethod | None = None
    webauthn_required: bool = False

    def has_permission(self, permission: str) -> bool:
        """True when the caller holds `permission`, or the platform wildcard."""
        return permission in self.permissions or self.is_platform_operator

    @property
    def is_platform_operator(self) -> bool:
        """True for `platform_super_admin`, the only cross-tenant principal."""
        return "platform:*" in self.permissions

    @property
    def step_up_method(self) -> MfaMethod | None:
        """The method a step-up must use: `webauthn`, or None when any factor will do."""
        return "webauthn" if self.webauthn_required or self.is_platform_operator else None

    def step_up_is_fresh(self, *, now: datetime | None = None, any_method: bool = False) -> bool:
        """True when MFA was verified, with an acceptable method, within the Section 7.3
        step-up window.

        `any_method` waives the WebAuthn requirement for one purpose only: enrolling a WebAuthn
        key. Otherwise a caller who must use WebAuthn but has none could never get one.
        """
        if not self.mfa_verified or self.mfa_verified_at is None:
            return False
        required = self.step_up_method
        if required is not None and self.mfa_method != required and not any_method:
            return False
        moment = now or datetime.now(UTC)
        return (moment - self.mfa_verified_at) <= STEP_UP_MAX_AGE

    def to_dict(self) -> dict[str, Any]:
        """Wire form, for identity-service introspection responses."""
        return {
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "permissions": sorted(self.permissions),
            "roles": sorted(self.roles),
            "auth_method": self.auth_method,
            "mfa_verified": self.mfa_verified,
            "mfa_verified_at": self.mfa_verified_at.isoformat() if self.mfa_verified_at else None,
            "session_id": self.session_id,
            "mfa_method": self.mfa_method,
            "webauthn_required": self.webauthn_required,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Principal:
        """Rebuild a principal from `to_dict` output. Raises on a malformed payload."""
        method = data["auth_method"]
        if method not in ("session", "api_key", "service_jwt"):
            raise ValueError("unknown auth_method")
        verified_at = data.get("mfa_verified_at")
        return cls(
            user_id=str(data["user_id"]),
            tenant_id=str(data["tenant_id"]),
            permissions=frozenset(str(p) for p in data.get("permissions", [])),
            auth_method=method,
            mfa_verified=bool(data.get("mfa_verified", False)),
            session_id=str(data["session_id"]) if data.get("session_id") else None,
            mfa_verified_at=datetime.fromisoformat(verified_at) if verified_at else None,
            roles=frozenset(str(r) for r in data.get("roles", [])),
            mfa_method=_mfa_method(data.get("mfa_method")),
            webauthn_required=bool(data.get("webauthn_required", False)),
        )


def _mfa_method(value: object) -> MfaMethod | None:
    if value is None:
        return None
    if value == "totp":
        return "totp"
    if value == "webauthn":
        return "webauthn"
    raise ValueError("unknown mfa_method")
