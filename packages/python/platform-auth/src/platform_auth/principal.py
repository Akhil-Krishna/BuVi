"""The authenticated caller, as every service sees it (Section 7.2).

`Principal` is the only representation of "who is calling" that crosses a
service boundary. It is deliberately small and frozen: a request handler cannot
widen its own authority by mutating it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final, Literal

AuthMethod = Literal["session", "api_key", "service_jwt"]

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
    """

    user_id: str
    tenant_id: str
    permissions: frozenset[str]
    auth_method: AuthMethod
    mfa_verified: bool
    session_id: str | None = None
    mfa_verified_at: datetime | None = None
    roles: frozenset[str] = field(default_factory=frozenset)

    def has_permission(self, permission: str) -> bool:
        """True when the caller holds `permission`, or the platform wildcard."""
        return permission in self.permissions or self.is_platform_operator

    @property
    def is_platform_operator(self) -> bool:
        """True for `platform_super_admin`, the only cross-tenant principal."""
        return "platform:*" in self.permissions

    def step_up_is_fresh(self, *, now: datetime | None = None) -> bool:
        """True when MFA was verified within the Section 7.3 step-up window."""
        if not self.mfa_verified or self.mfa_verified_at is None:
            return False
        moment = now or datetime.now(UTC)
        return (moment - self.mfa_verified_at) <= STEP_UP_MAX_AGE
