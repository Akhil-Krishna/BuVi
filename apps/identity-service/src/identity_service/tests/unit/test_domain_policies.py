"""Domain policy rules (Sections 2, 6.9, 7.3)."""

from __future__ import annotations

import datetime as dt

import pytest

from identity_service.domain.errors import (
    LastOrgAdminError,
    SelfServiceForbiddenError,
    UnknownRoleError,
)
from identity_service.domain.policies.roles import (
    RoleChange,
    apply_role_change,
    assert_not_self_target,
    assert_tenant_keeps_an_org_admin,
    default_role_for_jit_provisioning,
    validate_role_keys,
)
from identity_service.domain.policies.sessions import (
    SessionLifetime,
    absolute_expiry,
    is_expired,
)
from platform_auth import STEP_UP_MAX_AGE, Principal

pytestmark = pytest.mark.unit

LIFETIME = SessionLifetime.from_hours_and_days(12, 7)
NOW = dt.datetime(2026, 9, 14, 12, 0, tzinfo=dt.UTC)


# --- Role rules (Section 2) --------------------------------------------------


def test_unknown_role_is_rejected() -> None:
    """Section 37: no role outside Section 2 without an ADR."""
    with pytest.raises(UnknownRoleError):
        validate_role_keys(frozenset({"superuser"}))


def test_platform_super_admin_is_not_a_grantable_tenant_role() -> None:
    """A tenant admin must not be able to grant themselves cross-tenant reach."""
    with pytest.raises(UnknownRoleError):
        validate_role_keys(frozenset({"platform_super_admin"}))


def test_revocation_wins_over_grant() -> None:
    result = apply_role_change(
        frozenset({"client"}),
        RoleChange(grant=frozenset({"developer"}), revoke=frozenset({"developer"})),
    )
    assert result == frozenset({"client"})


def test_last_org_admin_cannot_be_demoted() -> None:
    with pytest.raises(LastOrgAdminError):
        assert_tenant_keeps_an_org_admin(
            org_admin_count=1, target_is_org_admin=True, target_remains_org_admin=False
        )


def test_second_to_last_org_admin_can_be_demoted() -> None:
    assert_tenant_keeps_an_org_admin(
        org_admin_count=2, target_is_org_admin=True, target_remains_org_admin=False
    )


def test_demoting_a_non_admin_is_always_allowed() -> None:
    assert_tenant_keeps_an_org_admin(
        org_admin_count=1, target_is_org_admin=False, target_remains_org_admin=False
    )


def test_admin_cannot_target_their_own_account() -> None:
    with pytest.raises(SelfServiceForbiddenError):
        assert_not_self_target(actor_user_id="u1", target_user_id="u1", operation="delete")


def test_admin_can_target_another_account() -> None:
    assert_not_self_target(actor_user_id="u1", target_user_id="u2", operation="delete")


def test_jit_default_role_is_client() -> None:
    """Section 6.4: a first federated login must not land on an admin role."""
    assert default_role_for_jit_provisioning() == "client"


# --- Session lifetime (Section 6.9) -------------------------------------------


def test_absolute_lifetime_is_seven_days() -> None:
    assert absolute_expiry(NOW, LIFETIME) == NOW + dt.timedelta(days=7)


def test_revoked_session_is_expired_immediately() -> None:
    assert is_expired(
        now=NOW,
        last_seen_at=NOW,
        expires_at=NOW + dt.timedelta(days=7),
        revoked_at=NOW - dt.timedelta(seconds=1),
        lifetime=LIFETIME,
    )


def test_session_expires_at_absolute_lifetime_even_when_active() -> None:
    assert is_expired(
        now=NOW,
        last_seen_at=NOW,
        expires_at=NOW,
        revoked_at=None,
        lifetime=LIFETIME,
    )


def test_idle_session_expires() -> None:
    assert is_expired(
        now=NOW,
        last_seen_at=NOW - dt.timedelta(hours=12, seconds=1),
        expires_at=NOW + dt.timedelta(days=7),
        revoked_at=None,
        lifetime=LIFETIME,
    )


def test_recently_used_session_is_live() -> None:
    assert not is_expired(
        now=NOW,
        last_seen_at=NOW - dt.timedelta(hours=11),
        expires_at=NOW + dt.timedelta(days=7),
        revoked_at=None,
        lifetime=LIFETIME,
    )


# --- Step-up freshness (Section 7.3) ------------------------------------------


def _principal(**overrides: object) -> Principal:
    defaults: dict[str, object] = {
        "user_id": "u1",
        "tenant_id": "t1",
        "permissions": frozenset({"user:manage"}),
        "auth_method": "session",
        "mfa_verified": True,
        "mfa_verified_at": NOW,
    }
    defaults.update(overrides)
    return Principal(**defaults)  # type: ignore[arg-type]


def test_step_up_is_fresh_inside_five_minutes() -> None:
    principal = _principal(mfa_verified_at=NOW - dt.timedelta(minutes=4, seconds=59))
    assert principal.step_up_is_fresh(now=NOW)


def test_step_up_is_stale_after_five_minutes() -> None:
    principal = _principal(mfa_verified_at=NOW - STEP_UP_MAX_AGE - dt.timedelta(seconds=1))
    assert not principal.step_up_is_fresh(now=NOW)


def test_step_up_without_mfa_is_never_fresh() -> None:
    assert not _principal(mfa_verified=False, mfa_verified_at=None).step_up_is_fresh(now=NOW)


def test_api_key_principal_can_never_satisfy_step_up() -> None:
    """Section 7.3 step-up is an interactive human control."""
    principal = _principal(auth_method="api_key", mfa_verified=False, mfa_verified_at=None)
    assert not principal.step_up_is_fresh(now=NOW)


def test_platform_operator_bypasses_tenant_check_only_via_wildcard() -> None:
    assert _principal(permissions=frozenset({"platform:*"})).is_platform_operator
    assert not _principal(permissions=frozenset({"user:manage"})).is_platform_operator
