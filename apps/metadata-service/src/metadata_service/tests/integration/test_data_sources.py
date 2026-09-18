"""Phase A3 DoD over HTTP: an `org_admin`/`developer` adds a Postgres data source, tests
it, syncs it, and reads tables and columns from the catalog API -- plus every failure
path those steps can take."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

from metadata_service.tests.conftest import (
    CUSTOMER_DB,
    EXPECTED_TABLES,
    READER_USER,
    FakeIdentity,
    Flows,
    PostgresInfo,
    make_settings,
    running_app,
)
from platform_auth import ServiceTokenIssuer
from platform_secrets import InMemorySecretStore, SecretStoreError

pytestmark = pytest.mark.integration


# --- The happy path, for both roles the DoD names ------------------------------------------


@pytest.mark.parametrize("role", ["org_admin", "developer"])
async def test_role_adds_tests_syncs_and_browses_a_postgres_data_source(
    role: str,
    client: httpx.AsyncClient,
    identity: FakeIdentity,
    secrets: InMemorySecretStore,
    flows: Flows,
    tenant: uuid.UUID,
) -> None:
    who = identity.add(tenant_id=tenant, roles={role})

    created = await client.post(
        "/api/v1/data-sources", json=flows.create_body(), headers=who.headers
    )
    assert created.status_code == 201, created.text
    source = created.json()
    assert source["status"] == "pending"
    assert source["tenant_id"] == str(tenant)
    assert source["created_by"] == str(who.user_id)
    assert source["last_sync_at"] is None
    assert "secret_ref" not in source
    source_id = source["id"]

    listed = await client.get("/api/v1/data-sources", headers=who.headers)
    assert [item["id"] for item in listed.json()["items"]] == [source_id]

    secret = await flows.set_secret(who, source_id)
    assert secret.status_code == 200, secret.text
    assert secret.json()["status"] == "pending"
    assert secrets.refs() == {f"tenants/{tenant}/datasources/{source_id}"}

    tested = await client.post(f"/api/v1/data-sources/{source_id}/test", headers=who.headers)
    assert tested.status_code == 200, tested.text
    result = tested.json()
    assert result["ok"] is True
    assert result["code"] == "CONNECTED"
    assert result["message"] == "Connected. 5 tables discovered."
    assert result["tables_discovered"] == 5
    assert result["status"] == "active"

    synced = await client.post(f"/api/v1/data-sources/{source_id}/sync", headers=who.headers)
    assert synced.status_code == 200, synced.text
    sync = synced.json()
    assert sync["ok"] is True and sync["code"] == "SYNCED"
    assert (sync["tables_synced"], sync["relationships_synced"]) == (5, 3)
    assert sync["columns_synced"] == 16
    assert sync["status"] == "active" and sync["snapshot_id"]

    read = (await client.get(f"/api/v1/data-sources/{source_id}", headers=who.headers)).json()
    assert read["status"] == "active" and read["last_sync_at"] is not None

    tables = (
        await client.get(f"/api/v1/data-sources/{source_id}/tables", headers=who.headers)
    ).json()
    assert [t["table_name"] for t in tables["items"]] == EXPECTED_TABLES
    assert {t["schema_name"] for t in tables["items"]} == {"sales"}
    by_name = {t["table_name"]: t for t in tables["items"]}
    assert by_name["regions"]["description"] == "Sales regions"
    assert by_name["regions"]["row_count_estimate"] == 2
    assert by_name["monthly_revenue"]["row_count_estimate"] is None

    detail_response = await client.get(
        f"/api/v1/data-sources/{source_id}/tables/{by_name['orders']['id']}", headers=who.headers
    )
    assert detail_response.status_code == 200, detail_response.text
    detail = detail_response.json()
    columns = {c["column_name"]: c for c in detail["columns"]}
    assert set(columns) == {"id", "customer_id", "order_date", "amount"}
    assert detail["column_count"] == 4
    assert columns["amount"]["data_type"] == "numeric(12,2)"
    assert columns["amount"]["description"] == "Order total in USD"
    assert columns["amount"]["is_pii"] is False
    relationships = {
        (
            r["from_column"]["table_name"],
            r["from_column"]["column_name"],
            r["to_column"]["table_name"],
            r["to_column"]["column_name"],
        )
        for r in detail["relationships"]
    }
    assert relationships == {
        ("orders", "customer_id", "customers", "id"),
        ("order_items", "order_id", "orders", "id"),
    }

    assert [e["event_type"] for e in identity.audit_events] == [
        "connection.created",
        "connection.secret_rotated",
    ]
    for event in identity.audit_events:
        assert event["tenant_id"] == str(tenant)
        assert event["actor_user_id"] == str(who.user_id)
        assert event["resource_type"] == "data_source"
        assert event["resource_id"] == source_id
    assert identity.audit_events[1]["before_state"] == {"status": "pending"}
    assert all(
        h["x-service-authorization"] == "Bearer svc:identity-service:identity-service:audit"
        for h in identity.audit_headers
    )


# --- Catalog sync semantics -------------------------------------------------------------------


async def test_resync_keeps_ids_and_user_flags_and_follows_schema_changes(
    client: httpx.AsyncClient,
    identity: FakeIdentity,
    flows: Flows,
    tenant: uuid.UUID,
    customer_db: Any,
    platform_db: Any,
) -> None:
    schema = f"s_{uuid.uuid4().hex[:10]}"
    await customer_db.execute(
        f"""
        CREATE SCHEMA {schema};
        CREATE TABLE {schema}.t1 (id integer PRIMARY KEY, a text);
        CREATE TABLE {schema}.t2 (id integer PRIMARY KEY, t1_id integer REFERENCES {schema}.t1 (id));
        GRANT USAGE ON SCHEMA {schema} TO {READER_USER};
        GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {READER_USER};
        """
    )
    who = identity.add(tenant_id=tenant, roles={"developer"})
    source = await flows.create(who, allowed_schemas=[schema])
    assert (await flows.set_secret(who, source["id"])).status_code == 200

    async def sync() -> dict[str, Any]:
        response = await client.post(
            f"/api/v1/data-sources/{source['id']}/sync", headers=who.headers
        )
        assert response.status_code == 200 and response.json()["ok"], response.text
        body: dict[str, Any] = response.json()
        return body

    async def table_ids() -> dict[str, str]:
        items = (
            await client.get(f"/api/v1/data-sources/{source['id']}/tables", headers=who.headers)
        ).json()["items"]
        return {t["table_name"]: t["id"] for t in items}

    first = await sync()
    assert (first["tables_synced"], first["relationships_synced"]) == (2, 1)
    ids = await table_ids()
    await platform_db.execute(
        "UPDATE metadata.tables SET is_visible_to_agent = false WHERE id = $1", uuid.UUID(ids["t1"])
    )

    await sync()
    assert await table_ids() == ids

    await customer_db.execute(
        f"""
        ALTER TABLE {schema}.t1 ADD COLUMN b numeric;
        DROP TABLE {schema}.t2;
        CREATE TABLE {schema}.t3 (id integer PRIMARY KEY);
        GRANT SELECT ON {schema}.t3 TO {READER_USER};
        """
    )
    third = await sync()
    assert (third["tables_synced"], third["relationships_synced"]) == (2, 0)
    after = await table_ids()
    assert set(after) == {"t1", "t3"} and after["t1"] == ids["t1"]
    t1 = (
        await client.get(
            f"/api/v1/data-sources/{source['id']}/tables/{ids['t1']}", headers=who.headers
        )
    ).json()
    assert [c["column_name"] for c in t1["columns"]] == ["a", "b", "id"]
    assert t1["is_visible_to_agent"] is False

    checksums = [
        row["checksum"]
        for row in await platform_db.fetch(
            "SELECT checksum FROM metadata.schema_snapshots WHERE data_source_id = $1 "
            "ORDER BY snapshot_at",
            uuid.UUID(source["id"]),
        )
    ]
    assert len(checksums) == 3
    assert checksums[0] == checksums[1] != checksums[2]


async def test_concurrent_syncs_serialize_without_duplicating_the_catalog(
    client: httpx.AsyncClient, identity: FakeIdentity, flows: Flows, tenant: uuid.UUID
) -> None:
    who = identity.add(tenant_id=tenant, roles={"org_admin"})
    source = await flows.create(who)
    assert (await flows.set_secret(who, source["id"])).status_code == 200
    url = f"/api/v1/data-sources/{source['id']}/sync"
    responses = await asyncio.gather(*(client.post(url, headers=who.headers) for _ in range(3)))
    assert all(r.status_code == 200 and r.json()["ok"] for r in responses), [
        r.text for r in responses
    ]
    tables = (
        await client.get(f"/api/v1/data-sources/{source['id']}/tables", headers=who.headers)
    ).json()["items"]
    assert [t["table_name"] for t in tables] == EXPECTED_TABLES


async def test_tables_paginate_with_an_opaque_cursor(
    client: httpx.AsyncClient, identity: FakeIdentity, flows: Flows, tenant: uuid.UUID
) -> None:
    who = identity.add(tenant_id=tenant, roles={"developer"})
    source = await flows.synced(who)
    url = f"/api/v1/data-sources/{source['id']}/tables"
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(5):
        params: dict[str, Any] = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        page = (await client.get(url, params=params, headers=who.headers)).json()
        seen += [t["table_name"] for t in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == EXPECTED_TABLES

    bad = await client.get(url, params={"cursor": "not-a-cursor!"}, headers=who.headers)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_CURSOR"
    over = await client.get(url, params={"limit": 201}, headers=who.headers)
    assert over.status_code == 422


async def test_data_source_list_paginates(
    client: httpx.AsyncClient, identity: FakeIdentity, flows: Flows, tenant: uuid.UUID
) -> None:
    who = identity.add(tenant_id=tenant, roles={"org_admin"})
    created = {(await flows.create(who, name=f"source-{i}"))["id"] for i in range(3)}
    first = (
        await client.get("/api/v1/data-sources", params={"limit": 2}, headers=who.headers)
    ).json()
    second = (
        await client.get(
            "/api/v1/data-sources",
            params={"limit": 2, "cursor": first["next_cursor"]},
            headers=who.headers,
        )
    ).json()
    assert len(first["items"]) == 2 and second["next_cursor"] is None
    assert {i["id"] for i in first["items"] + second["items"]} == created


# --- Validation and refusals --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"engine": "snowflake"}, "ENGINE_NOT_SUPPORTED"),
        ({"engine": "oracle"}, "VALIDATION_FAILED"),
        ({"host_label": "postgres://reader:pw@db:5432/sales"}, "VALIDATION_FAILED"),
        ({"allowed_schemas": []}, "VALIDATION_FAILED"),
        ({"allowed_schemas": ["pg_catalog"]}, "VALIDATION_FAILED"),
        ({"allowed_schemas": ["sales", "sales"]}, "VALIDATION_FAILED"),
        ({"database_name": "sales; DROP"}, "VALIDATION_FAILED"),
        ({"password": "smuggled"}, "VALIDATION_FAILED"),
        ({"secret_ref": "secret/data/tenants/other"}, "VALIDATION_FAILED"),
    ],
)
async def test_create_refuses_invalid_payloads_and_stores_nothing(
    client: httpx.AsyncClient,
    identity: FakeIdentity,
    flows: Flows,
    tenant: uuid.UUID,
    overrides: dict[str, Any],
    code: str,
) -> None:
    who = identity.add(tenant_id=tenant, roles={"org_admin"})
    response = await client.post(
        "/api/v1/data-sources", json=flows.create_body(**overrides), headers=who.headers
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == code
    assert "smuggled" not in response.text and "pw@" not in response.text
    listed = await client.get("/api/v1/data-sources", headers=who.headers)
    assert listed.json()["items"] == []


async def test_test_and_sync_require_credentials_first(
    client: httpx.AsyncClient, identity: FakeIdentity, flows: Flows, tenant: uuid.UUID
) -> None:
    who = identity.add(tenant_id=tenant, roles={"developer"})
    source = await flows.create(who)
    for action in ("test", "sync"):
        response = await client.post(
            f"/api/v1/data-sources/{source['id']}/{action}", headers=who.headers
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "SECRET_NOT_CONFIGURED"


async def test_disabled_data_source_refuses_every_operation(
    client: httpx.AsyncClient,
    identity: FakeIdentity,
    flows: Flows,
    tenant: uuid.UUID,
    platform_db: Any,
) -> None:
    who = identity.add(tenant_id=tenant, roles={"org_admin"})
    source = await flows.synced(who)
    await platform_db.execute(
        "UPDATE metadata.data_sources SET status = 'disabled' WHERE id = $1",
        uuid.UUID(source["id"]),
    )
    for action in ("test", "sync"):
        response = await client.post(
            f"/api/v1/data-sources/{source['id']}/{action}", headers=who.headers
        )
        assert (
            response.status_code == 409
            and response.json()["error"]["code"] == "DATA_SOURCE_DISABLED"
        )
    secret = await flows.set_secret(who, source["id"])
    assert secret.status_code == 409


@pytest.mark.parametrize(
    ("create", "secret", "code"),
    [
        ({}, {"password": "Wr0ng-Pa55word-91fe"}, "AUTHENTICATION_FAILED"),
        ({}, {"username": "no_such_role"}, "AUTHENTICATION_FAILED"),
        ({"database_name": "no_such_database"}, {}, "DATABASE_NOT_FOUND"),
        ({}, {"port": 1}, "HOST_UNREACHABLE"),
        ({}, {"sslmode": "require"}, "TLS_ERROR"),
    ],
    ids=["wrong-password", "unknown-user", "unknown-database", "closed-port", "ssl-refused"],
)
async def test_connection_failures_return_sanitized_diagnostics(
    client: httpx.AsyncClient,
    identity: FakeIdentity,
    flows: Flows,
    postgres: PostgresInfo,
    tenant: uuid.UUID,
    create: dict[str, Any],
    secret: dict[str, Any],
    code: str,
) -> None:
    who = identity.add(tenant_id=tenant, roles={"developer"})
    source = await flows.create(who, **create)
    assert (await flows.set_secret(who, source["id"], **secret)).status_code == 200

    tested = await client.post(f"/api/v1/data-sources/{source['id']}/test", headers=who.headers)
    assert tested.status_code == 200, tested.text
    result = tested.json()
    assert (result["ok"], result["code"], result["status"]) == (False, code, "error")
    assert result["tables_discovered"] is None

    synced = await client.post(f"/api/v1/data-sources/{source['id']}/sync", headers=who.headers)
    assert synced.status_code == 200
    assert (synced.json()["ok"], synced.json()["code"]) == (False, code)
    assert synced.json()["message"] == result["message"]

    leaks = [READER_USER, "no_such_role", "Wr0ng-Pa55word", f"{postgres.host}:", CUSTOMER_DB]
    for text in (tested.text, synced.text):
        for leak in leaks:
            assert leak not in text, (code, leak)

    healed = await flows.set_secret(who, source["id"])
    assert healed.status_code == 200 and healed.json()["status"] == "pending"


async def test_private_destinations_are_refused_without_an_allow_list(
    postgres: PostgresInfo,
    identity: FakeIdentity,
    secrets: InMemorySecretStore,
    gateway_issuer: ServiceTokenIssuer,
    tenant: uuid.UUID,
) -> None:
    settings = make_settings(postgres, connector_allowed_internal_hosts=[])
    async with running_app(settings, identity, secrets, gateway_issuer) as (_, client):
        flows = Flows(client, postgres)
        who = identity.add(tenant_id=tenant, roles={"org_admin"})
        source = await flows.create(who)

        for literal in ("127.0.0.1", "169.254.169.254", "10.0.0.8"):
            refused = await flows.set_secret(who, source["id"], host=literal)
            assert refused.status_code == 422, refused.text
            assert refused.json()["error"]["code"] == "DESTINATION_NOT_ALLOWED"
        assert secrets.refs() == frozenset()

        # A hostname is accepted at write time and judged on its resolved addresses.
        assert (await flows.set_secret(who, source["id"], host="localhost")).status_code == 200
        tested = (
            await client.post(f"/api/v1/data-sources/{source['id']}/test", headers=who.headers)
        ).json()
        assert (tested["ok"], tested["code"], tested["status"]) == (
            False,
            "DESTINATION_NOT_ALLOWED",
            "error",
        )
        synced = (
            await client.post(f"/api/v1/data-sources/{source['id']}/sync", headers=who.headers)
        ).json()
        assert synced["code"] == "DESTINATION_NOT_ALLOWED"


class _BrokenSecretStore(InMemorySecretStore):
    async def write(self, ref: str, value: dict[str, str]) -> None:
        raise SecretStoreError("secret write failed with status 500")

    async def read(self, ref: str) -> dict[str, str] | None:
        raise SecretStoreError("secret read failed with status 500")

    async def ping(self) -> bool:
        return False


async def test_secret_store_outage_is_503_and_changes_nothing(
    postgres: PostgresInfo,
    identity: FakeIdentity,
    gateway_issuer: ServiceTokenIssuer,
    tenant: uuid.UUID,
) -> None:
    async with running_app(
        make_settings(postgres), identity, _BrokenSecretStore(), gateway_issuer
    ) as (_, client):
        flows = Flows(client, postgres)
        who = identity.add(tenant_id=tenant, roles={"org_admin"})
        source = await flows.create(who)
        secret = await flows.set_secret(who, source["id"])
        assert (
            secret.status_code == 503
            and secret.json()["error"]["code"] == "SECRET_STORE_UNAVAILABLE"
        )
        tested = await client.post(f"/api/v1/data-sources/{source['id']}/test", headers=who.headers)
        assert tested.status_code == 503
        assert [e["event_type"] for e in identity.audit_events] == ["connection.created"]

        ready = await client.get("/health/ready")
        assert ready.status_code == 200 and ready.json()["status"] == "degraded"


async def test_audit_outage_is_logged_but_does_not_undo_the_operation(
    client: httpx.AsyncClient,
    identity: FakeIdentity,
    flows: Flows,
    tenant: uuid.UUID,
    captured_logs: list[str],
) -> None:
    identity.audit_status = 503
    who = identity.add(tenant_id=tenant, roles={"org_admin"})
    source = await flows.create(who)
    listed = await client.get("/api/v1/data-sources", headers=who.headers)
    assert [i["id"] for i in listed.json()["items"]] == [source["id"]]
    failures = [
        line
        for line in captured_logs
        if "audit event delivery failed" in line and '"level": "ERROR"' in line
    ]
    assert failures and "connection.created" in failures[0]


# --- Authentication and the gateway boundary ------------------------------------------------------


async def test_authentication_failures_map_to_the_right_envelope(
    client: httpx.AsyncClient, identity: FakeIdentity
) -> None:
    anonymous = await client.get("/api/v1/data-sources")
    assert (
        anonymous.status_code == 401
        and anonymous.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    )
    assert identity.introspections == 0

    bogus = await client.get("/api/v1/data-sources", headers={"Cookie": "buvi_session=nope"})
    assert bogus.status_code == 401

    inactive = await client.get(
        "/api/v1/data-sources", headers={"Authorization": "Bearer inactive"}
    )
    assert inactive.status_code == 403 and inactive.json()["error"]["code"] == "USER_NOT_ACTIVE"

    identity.introspect_status = 500
    broken = await client.get("/api/v1/data-sources", headers={"Cookie": "buvi_session=x"})
    assert broken.status_code == 502 and broken.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"


async def test_gateway_token_is_required_when_configured_and_gates_forwarded_ip(
    postgres: PostgresInfo,
    identity: FakeIdentity,
    secrets: InMemorySecretStore,
    gateway_issuer: ServiceTokenIssuer,
    tenant: uuid.UUID,
) -> None:
    def token(audience: str = "metadata-service", scope: str = "metadata-service:proxy") -> str:
        issued = gateway_issuer.issue(
            subject="api-gateway", audience=audience, scopes=frozenset({scope})
        )
        return f"Bearer {issued}"

    who = identity.add(tenant_id=tenant, roles={"org_admin"})
    settings = make_settings(postgres, require_gateway_token=True)
    async with running_app(settings, identity, secrets, gateway_issuer) as (_, client):
        cases = [
            ({}, 401),
            ({"X-Service-Authorization": "Bearer forged"}, 401),
            ({"X-Service-Authorization": token(audience="identity-service")}, 401),
            ({"X-Service-Authorization": token(scope="identity-service:proxy")}, 403),
        ]
        for extra, status in cases:
            response = await client.get("/api/v1/data-sources", headers={**who.headers, **extra})
            assert response.status_code == status, (extra, response.text)

        created = await client.post(
            "/api/v1/data-sources",
            json=Flows.create_body(),
            headers={
                **who.headers,
                "X-Service-Authorization": token(),
                "X-Forwarded-For": "198.51.100.23",
                "X-Request-ID": "req_gateway_forwarded_01",
            },
        )
        assert created.status_code == 201, created.text
        assert created.headers["x-request-id"] == "req_gateway_forwarded_01"
    assert identity.audit_events[-1]["ip_address"] == "198.51.100.23"
    assert identity.audit_headers[-1]["x-request-id"] == "req_gateway_forwarded_01"


async def test_forwarded_ip_is_ignored_without_a_gateway_token(
    client: httpx.AsyncClient, identity: FakeIdentity, tenant: uuid.UUID
) -> None:
    who = identity.add(tenant_id=tenant, roles={"org_admin"})
    response = await client.post(
        "/api/v1/data-sources",
        json=Flows.create_body(),
        headers={**who.headers, "X-Forwarded-For": "6.6.6.6"},
    )
    assert response.status_code == 201
    assert identity.audit_events[-1]["ip_address"] != "6.6.6.6"


async def test_health_probes(client: httpx.AsyncClient) -> None:
    live = await client.get("/health/live")
    assert live.json() == {"status": "ok", "service": "metadata-service", "checks": {}}
    ready = await client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"] == {
        "database": "ok",
        "identity-service": "ok",
        "secret-store": "ok",
    }


def test_connector_never_logs_driver_messages() -> None:
    """Static guard for Section 13.1: the connector never logs an exception's text."""
    source = (
        Path(__file__).resolve().parents[2] / "infrastructure" / "connectors" / "postgres.py"
    ).read_text()
    assert "logger.exception" not in source
    assert "str(exc)" not in source
    assert "exc_info" not in source
