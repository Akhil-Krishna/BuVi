"""Section 9 Idempotency-Key rules and which catalog routes get which handling."""

from __future__ import annotations

import pytest

from api_gateway.domain.catalog import CATALOG, MUTATING_METHODS
from api_gateway.domain.policies.idempotency import fingerprint, is_final, record_key, valid_key

pytestmark = pytest.mark.unit


def test_key_syntax() -> None:
    assert valid_key("retry-7f3a") and valid_key("x" * 255)
    for bad in ("", "x" * 256, "has space", "tab\tkey", "ключ"):
        assert not valid_key(bad)


def test_record_scope_hashes_the_key_and_separates_principals() -> None:
    record = record_key("t1", "u1", "client-chosen-key")
    prefix, digest = record.rsplit(":", 1)
    assert prefix == "idem:t1:u1" and len(digest) == 64 and "client-chosen-key" not in record
    assert (
        len(
            {
                record,
                record_key("t1", "u2", "client-chosen-key"),
                record_key("t2", "u1", "client-chosen-key"),
            }
        )
        == 3
    )


def test_fingerprint_covers_method_route_query_and_body() -> None:
    base = fingerprint("POST", "/api/v1/dashboards", "", b'{"name":"a"}')
    assert base == fingerprint("post", "/api/v1/dashboards", "", b'{"name":"a"}')
    assert base != fingerprint("POST", "/api/v1/conversations", "", b'{"name":"a"}')
    assert base != fingerprint("POST", "/api/v1/dashboards", "x=1", b'{"name":"a"}')
    assert base != fingerprint("POST", "/api/v1/dashboards", "", b'{"name":"b"}')
    # Length-prefixed parts: shifting bytes between path and query is not a collision.
    assert fingerprint("POST", "/a", "b", b"") != fingerprint("POST", "/ab", "", b"")


def test_only_final_outcomes_are_recorded() -> None:
    assert all(is_final(s) for s in (200, 201, 202, 204, 400, 404, 410, 413, 422))
    assert not any(is_final(s) for s in (401, 403, 408, 409, 425, 429, 500, 502, 503, 504))


def test_every_mutating_non_public_route_accepts_a_key_and_secrets_are_never_stored() -> None:
    modes = {(r.method, r.path): r.idempotency_mode for r in CATALOG}
    for route in CATALOG:
        if route.method not in MUTATING_METHODS or route.public:
            assert route.idempotency_mode == "ignore", route.path
    assert {k for k, m in modes.items() if m == "no_store"} == {
        ("POST", "/auth/mfa/enroll"),
        ("POST", "/me/api-keys"),
        ("POST", "/dashboards/{id}/share-links"),
        ("POST", "/sql/execute"),
        ("POST", "/mcp/servers/{id}/tools/{tool}/invoke"),
        ("POST", "/admin/webhooks"),
    }
    ignored_mutations = {k for k, m in modes.items() if m == "ignore" and k[0] in MUTATING_METHODS}
    assert ignored_mutations == {
        ("POST", "/auth/logout"),
        ("POST", "/auth/mfa/verify"),
        ("POST", "/auth/mfa/challenge"),
        ("POST", "/invitations/{token}/accept"),
    }
