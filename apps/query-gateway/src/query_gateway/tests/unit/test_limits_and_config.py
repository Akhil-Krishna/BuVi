"""Concurrency limiter, credential payload parsing, result serialization, production safety."""

from __future__ import annotations

import datetime as dt
import decimal
import uuid

import pytest

from query_gateway.core.config import Settings
from query_gateway.domain.errors import QueryConcurrencyLimitedError
from query_gateway.domain.value_objects.execution import ConnectionCredentials
from query_gateway.infrastructure.cache.tenant_concurrency import TenantConcurrencyLimiter
from query_gateway.infrastructure.connectors.postgres import _json_value, credential_fingerprint

pytestmark = pytest.mark.unit


async def test_limiter_caps_per_tenant_and_releases() -> None:
    limiter = TenantConcurrencyLimiter(limit=2)
    a, b = uuid.uuid4(), uuid.uuid4()
    async with limiter.slot(a), limiter.slot(a):
        with pytest.raises(QueryConcurrencyLimitedError):
            async with limiter.slot(a):
                pass
        async with limiter.slot(b):
            assert limiter.active(b) == 1
    assert limiter.active(a) == 0
    with pytest.raises(RuntimeError):
        async with limiter.slot(a):
            raise RuntimeError("boom")
    assert limiter.active(a) == 0


def test_credentials_parse_and_hide() -> None:
    secret = "S3cret-" + uuid.uuid4().hex
    payload = {
        "host": "db.example.com",
        "port": "5432",
        "username": "reader",
        "password": secret,
        "sslmode": "require",
    }
    creds = ConnectionCredentials.from_secret_payload(payload)
    assert secret not in repr(creds) and "reader" not in repr(creds)
    assert credential_fingerprint(creds) != credential_fingerprint(
        ConnectionCredentials.from_secret_payload({**payload, "password": "other"})
    )
    for bad in (
        {**payload, "port": "x"},
        {**payload, "sslmode": "allow"},
        {k: v for k, v in payload.items() if k != "password"},
    ):
        with pytest.raises(ValueError) as info:
            ConnectionCredentials.from_secret_payload(bad)
        assert secret not in str(info.value)


def test_result_values_are_json_safe() -> None:
    assert _json_value(decimal.Decimal("1.50")) == "1.50"
    assert _json_value(dt.date(2026, 4, 1)) == "2026-04-01"
    assert _json_value(b"\x00\x01") == "AAE="
    assert _json_value([uuid.UUID(int=0)]) == ["00000000-0000-0000-0000-000000000000"]
    assert _json_value({"k": dt.timedelta(seconds=2)}) == {"k": 2.0}


def test_dev_defaults_are_refused_in_prod() -> None:
    with pytest.raises(RuntimeError) as info:
        Settings(environment="prod").assert_production_safe()
    for problem in (
        "vault_token",
        "service_client_secret",
        "result store credentials",
        "result_store_secure",
        "loopback",
    ):
        assert problem in str(info.value)
    assert "minioadmin" not in str(info.value)
