"""Section 7.3 step-up, proven by method (Phase A10): a step-up with the wrong factor is stale."""

from __future__ import annotations

import datetime as dt

import pytest

from platform_auth import Principal, StepUpRequiredError

NOW = dt.datetime(2026, 9, 18, 12, 0, tzinfo=dt.UTC)


def _principal(
    *,
    method: str | None = "totp",
    age: dt.timedelta = dt.timedelta(minutes=1),
    webauthn_required: bool = False,
    permissions: frozenset[str] = frozenset(),
) -> Principal:
    return Principal(
        user_id="u",
        tenant_id="t",
        permissions=permissions,
        auth_method="session",
        mfa_verified=method is not None,
        mfa_verified_at=NOW - age if method is not None else None,
        mfa_method=method,  # type: ignore[arg-type]
        webauthn_required=webauthn_required,
    )


@pytest.mark.parametrize(
    ("method", "age", "webauthn_required", "fresh"),
    [
        ("totp", dt.timedelta(minutes=1), False, True),
        ("webauthn", dt.timedelta(minutes=1), False, True),
        ("totp", dt.timedelta(minutes=6), False, False),  # stale
        (None, dt.timedelta(0), False, False),  # never verified
        ("totp", dt.timedelta(minutes=1), True, False),  # wrong method under the policy
        ("webauthn", dt.timedelta(minutes=1), True, True),
        ("webauthn", dt.timedelta(minutes=6), True, False),
    ],
)
def test_freshness_needs_time_and_the_required_method(
    method: str | None, age: dt.timedelta, webauthn_required: bool, fresh: bool
) -> None:
    principal = _principal(method=method, age=age, webauthn_required=webauthn_required)
    assert principal.step_up_is_fresh(now=NOW) is fresh


def test_platform_operators_always_need_webauthn_whatever_the_payload_says() -> None:
    """Section 6.6: even if identity-service omitted the flag, TOTP never suffices."""
    operator = _principal(method="totp", permissions=frozenset({"platform:*"}))
    assert operator.step_up_method == "webauthn"
    assert not operator.step_up_is_fresh(now=NOW)
    assert _principal(method="webauthn", permissions=frozenset({"platform:*"})).step_up_is_fresh(
        now=NOW
    )


def test_wire_form_round_trips_and_refuses_unknown_methods() -> None:
    principal = _principal(method="webauthn", webauthn_required=True)
    assert Principal.from_dict(principal.to_dict()) == principal
    legacy = principal.to_dict()
    del legacy["mfa_method"], legacy["webauthn_required"]
    assert Principal.from_dict(legacy).webauthn_required is False  # older payloads still parse
    with pytest.raises(ValueError):
        Principal.from_dict({**principal.to_dict(), "mfa_method": "sms"})


def test_the_refusal_tells_the_client_which_factor_to_use() -> None:
    error = StepUpRequiredError.for_principal(_principal(webauthn_required=True))
    assert (error.code, error.status_code) == ("STEP_UP_REQUIRED", 403)
    assert error.details == {"method": "webauthn"}
    assert error.headers["WWW-Authenticate"] == 'MFA realm="step-up", max_age=300'
    assert StepUpRequiredError.for_principal(_principal()).details == {"method": "any"}


def test_any_method_waives_only_the_webauthn_requirement() -> None:
    """For enrolling a WebAuthn key: a fresh TOTP check suffices, a stale one never does."""
    required = _principal(method="totp", webauthn_required=True)
    assert not required.step_up_is_fresh(now=NOW)
    assert required.step_up_is_fresh(now=NOW, any_method=True)
    stale = _principal(method="totp", age=dt.timedelta(minutes=6), webauthn_required=True)
    assert not stale.step_up_is_fresh(now=NOW, any_method=True)
