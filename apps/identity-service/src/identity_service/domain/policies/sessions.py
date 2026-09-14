"""Session lifetime rules (Section 6.9).

Both limits are enforced here, server-side, independent of whatever lifetime the
identity provider put on its own tokens -- Section 6.9 is explicit that the
platform does not inherit the IdP's session policy.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True)
class SessionLifetime:
    """Idle and absolute limits, from configuration."""

    idle_timeout: dt.timedelta
    absolute_lifetime: dt.timedelta

    @classmethod
    def from_hours_and_days(cls, idle_hours: int, absolute_days: int) -> SessionLifetime:
        return cls(
            idle_timeout=dt.timedelta(hours=idle_hours),
            absolute_lifetime=dt.timedelta(days=absolute_days),
        )


def absolute_expiry(created_at: dt.datetime, lifetime: SessionLifetime) -> dt.datetime:
    """The hard end of a session, set once at creation and never extended."""
    return created_at + lifetime.absolute_lifetime


def is_expired(
    *,
    now: dt.datetime,
    last_seen_at: dt.datetime,
    expires_at: dt.datetime,
    revoked_at: dt.datetime | None,
    lifetime: SessionLifetime,
) -> bool:
    """True when a session may no longer be used.

    Three independent reasons, checked in the order that costs least: explicit
    revocation, the absolute lifetime, then idleness.
    """
    if revoked_at is not None:
        return True
    if now >= expires_at:
        return True
    return (now - last_seen_at) >= lifetime.idle_timeout
