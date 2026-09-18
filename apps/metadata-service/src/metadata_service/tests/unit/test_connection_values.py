"""Connection value objects: nothing secret is displayable, nothing DSN-shaped is stored."""

from __future__ import annotations

import uuid

import pytest

from metadata_service.domain.value_objects.connection import (
    ConnectionSecret,
    ConnectionTarget,
    data_source_secret_ref,
    validate_database_name,
    validate_host,
    validate_host_label,
    validate_schema_name,
)

pytestmark = pytest.mark.unit

PASSWORD = "S3cret-value-never-printed"


def _secret(**overrides: object) -> ConnectionSecret:
    values: dict[str, object] = {
        "host": "db.example.com",
        "port": 5432,
        "username": "reader",
        "password": PASSWORD,
        "sslmode": "require",
    }
    values.update(overrides)
    return ConnectionSecret(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "label",
    [
        "postgres://reader:pw@db.example.com:5432/sales",
        "reader@db.example.com",
        "host=db.example.com user=reader",
        "sales password",
        "my pwd here",
        "",
        "x" * 201,
        "tab\tlabel",
    ],
)
def test_host_label_refuses_connection_strings(label: str) -> None:
    with pytest.raises(ValueError):
        validate_host_label(label)


def test_host_label_accepts_display_text() -> None:
    assert validate_host_label("  Sales warehouse (us-east-1) ") == "Sales warehouse (us-east-1)"


@pytest.mark.parametrize(
    "name",
    [
        "pg_catalog",
        "PG_TOAST",
        "information_schema",
        "mysql",
        "performance_schema",
        "SYS",
        'sales"; drop',
        "1sales",
        "a" * 64,
    ],
)
def test_schema_names_refuse_system_schemas_and_quoting(name: str) -> None:
    with pytest.raises(ValueError):
        validate_schema_name(name)


@pytest.mark.parametrize(
    "host", ["db.example.com/x", "user@db", "db example", "-db", "a" * 254, ""]
)
def test_hosts_refuse_anything_but_a_hostname_or_ip(host: str) -> None:
    with pytest.raises(ValueError):
        validate_host(host)


def test_hosts_normalise() -> None:
    assert validate_host("DB.Example.com.") == "db.example.com"
    assert validate_host("::1") == "::1"
    assert validate_host("sample-sales-db") == "sample-sales-db"


def test_database_name() -> None:
    assert validate_database_name("sample_sales") == "sample_sales"
    with pytest.raises(ValueError):
        validate_database_name("sales;drop")


def test_secret_repr_reveals_no_field() -> None:
    secret = _secret()
    target = ConnectionTarget("postgres", "sales", ("sales",), secret)
    for rendered in (repr(secret), str(secret), repr(target)):
        assert PASSWORD not in rendered
        assert "db.example.com" not in rendered
        assert "reader" not in rendered


def test_secret_payload_round_trips() -> None:
    secret = _secret(sslmode="verify-full", port=6543)
    assert ConnectionSecret.from_secret_payload(secret.to_secret_payload()) == secret


@pytest.mark.parametrize(
    "payload",
    [
        {"host": "db", "port": "x", "username": "u", "password": PASSWORD, "sslmode": "require"},
        {"host": "db", "port": "5432", "username": "u", "password": PASSWORD, "sslmode": "allow"},
        {"host": "db", "port": "5432", "username": "u", "sslmode": "require"},
        {
            "host": "db/../x",
            "port": "5432",
            "username": "u",
            "password": PASSWORD,
            "sslmode": "require",
        },
    ],
)
def test_malformed_payload_fails_without_echoing_values(payload: dict[str, str]) -> None:
    with pytest.raises(ValueError) as info:
        ConnectionSecret.from_secret_payload(payload)
    assert PASSWORD not in str(info.value)
    assert info.value.__cause__ is None


@pytest.mark.parametrize(
    "overrides", [{"port": 0}, {"port": 70000}, {"password": ""}, {"sslmode": "prefer"}]
)
def test_secret_invariants(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _secret(**overrides)


def test_secret_ref_layout_matches_section_8_2() -> None:
    tenant, source = uuid.uuid4(), uuid.uuid4()
    assert data_source_secret_ref(tenant, source) == f"tenants/{tenant}/datasources/{source}"
