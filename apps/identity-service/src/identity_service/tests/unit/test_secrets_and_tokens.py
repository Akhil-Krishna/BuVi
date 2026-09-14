"""Token handling and log redaction (Sections 6.7, 6.8, 21, 24)."""

from __future__ import annotations

import pytest

from identity_service.core.logging import REDACTED, redact
from identity_service.domain.value_objects.pkce import create_pkce_challenge
from identity_service.domain.value_objects.tokens import (
    API_KEY_PREFIX_RANDOM_CHARS,
    generate_api_key,
    generate_token,
    hash_secret,
    hash_token,
    tokens_match,
    verify_secret,
)

pytestmark = pytest.mark.unit


# --- Tokens -------------------------------------------------------------------


def test_tokens_are_unique_and_high_entropy() -> None:
    tokens = {generate_token() for _ in range(200)}
    assert len(tokens) == 200
    assert all(len(token) >= 43 for token in tokens)


def test_token_hash_is_not_the_token() -> None:
    """A database dump must not yield a usable credential (Section 24)."""
    token = generate_token()
    assert hash_token(token) != token


def test_tokens_match_only_for_the_right_token() -> None:
    token = generate_token()
    stored = hash_token(token)
    assert tokens_match(token, stored)
    assert not tokens_match(generate_token(), stored)


def test_api_key_secret_is_hashed_with_argon2() -> None:
    """Section 6.8: stored hashed, never reversible."""
    full_key, _ = generate_api_key("sk_live_")
    stored = hash_secret(full_key)
    assert stored.startswith("$argon2")
    assert full_key not in stored
    assert verify_secret(full_key, stored)
    assert not verify_secret(full_key + "x", stored)


def test_verify_secret_returns_false_on_a_malformed_hash() -> None:
    """A corrupt hash must fail closed rather than raise into a 500."""
    assert not verify_secret("anything", "not-a-hash")


def test_api_key_prefix_distinguishes_two_keys() -> None:
    """Section 8.1 calls key_prefix the value shown in the UI.

    An 8-character prefix is exactly `sk_live_`, which is identical for every
    key and so cannot identify one in a list. Six random characters are kept
    alongside it; the prefix stays non-secret because the remaining ~250 bits
    are what argon2 verifies. See ADR 0002.
    """
    first_key, first_prefix = generate_api_key("sk_live_")
    _, second_prefix = generate_api_key("sk_live_")

    assert first_prefix != second_prefix
    assert first_prefix.startswith("sk_live_")
    assert len(first_prefix) == len("sk_live_") + API_KEY_PREFIX_RANDOM_CHARS
    assert first_key.startswith(first_prefix)
    assert len(first_key) > len(first_prefix) + 30


# --- PKCE (Section 6.1, RFC 7636) ---------------------------------------------


def test_pkce_challenge_is_s256_and_unpadded() -> None:
    challenge = create_pkce_challenge()
    assert challenge.method == "S256"
    assert "=" not in challenge.challenge
    assert 43 <= len(challenge.verifier) <= 128


def test_pkce_verifier_and_state_differ_per_request() -> None:
    first, second = create_pkce_challenge(), create_pkce_challenge()
    assert first.verifier != second.verifier
    assert first.state != second.state
    assert first.nonce != second.nonce


# --- Redaction (Section 24) ----------------------------------------------------


def test_redact_masks_secret_shaped_keys() -> None:
    payload = {
        "email": "user@example.com",
        "password": "hunter2",
        "refresh_token": "rt_live_abc",
        "api_key": "sk_live_abc",
        "totp_secret": "JBSWY3DP",
    }
    result = redact(payload)
    assert result["email"] == "user@example.com"
    for key in ("password", "refresh_token", "api_key", "totp_secret"):
        assert result[key] == REDACTED


def test_redact_recurses_into_nested_structures() -> None:
    """An audit before/after diff is nested, so shallow redaction is not enough."""
    payload = {"before": {"connection": {"dsn": "postgres://u:p@h/db"}}, "items": [{"token": "t"}]}
    result = redact(payload)
    assert result["before"]["connection"]["dsn"] == REDACTED
    assert result["items"][0]["token"] == REDACTED


def test_redact_is_case_insensitive() -> None:
    assert redact({"Authorization": "Bearer x"})["Authorization"] == REDACTED
