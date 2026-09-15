"""Phase A3 DoD: "secret never appears in any API response or log".

Drives every endpoint through success and failure paths with a recognisable password,
then searches everything the service emits or persists for it: every HTTP response,
every log record at every level (formatted and raw), every row of the `metadata` schema,
and every audit payload sent to identity-service. The only place the credentials may
exist is the secret store -- which the test checks too, as a positive control.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest

from metadata_service.tests.conftest import (
    READER_PASSWORD,
    READER_USER,
    FakeIdentity,
    Flows,
    PostgresInfo,
)
from platform_secrets import InMemorySecretStore

pytestmark = [pytest.mark.integration, pytest.mark.security]

#: Generated per run: unique to search for, and no credential-shaped literal in the repo.
WRONG_PASSWORD = f"wrong-{uuid.uuid4().hex}"


async def test_credentials_never_leave_the_secret_store(
    client: httpx.AsyncClient,
    identity: FakeIdentity,
    secrets: InMemorySecretStore,
    flows: Flows,
    postgres: PostgresInfo,
    tenant: uuid.UUID,
    platform_db: Any,
    captured_logs: list[str],
) -> None:
    who = identity.add(tenant_id=tenant, roles={"org_admin"})
    responses: list[httpx.Response] = []

    async def call(method: str, url: str, **kwargs: Any) -> httpx.Response:
        response = await client.request(method, url, headers=who.headers, **kwargs)
        responses.append(response)
        return response

    created = (await call("POST", "/api/v1/data-sources", json=flows.create_body())).json()
    base = f"/api/v1/data-sources/{created['id']}"

    # Success path.
    await call("POST", f"{base}/secret", json=flows.secret_body())
    await call("POST", f"{base}/test")
    await call("POST", f"{base}/sync")
    await call("GET", "/api/v1/data-sources")
    await call("GET", base)
    tables = (await call("GET", f"{base}/tables")).json()["items"]
    await call("GET", f"{base}/tables/{tables[0]['id']}")

    # Failure paths: wrong password, closed port, TLS refused, malformed requests.
    await call("POST", f"{base}/secret", json=flows.secret_body(password=WRONG_PASSWORD))
    await call("POST", f"{base}/test")
    await call("POST", f"{base}/sync")
    await call("POST", f"{base}/secret", json=flows.secret_body(port=1))
    await call("POST", f"{base}/test")
    await call("POST", f"{base}/secret", json=flows.secret_body(sslmode="require"))
    await call("POST", f"{base}/sync")
    invalid = await call(
        "POST", f"{base}/secret", json={**flows.secret_body(password=WRONG_PASSWORD), "port": "x"}
    )
    assert invalid.status_code == 422
    smuggled = await call(
        "POST", "/api/v1/data-sources", json={**flows.create_body(), "password": READER_PASSWORD}
    )
    assert smuggled.status_code == 422

    # Leave valid credentials in place for the positive control.
    await call("POST", f"{base}/secret", json=flows.secret_body())
    assert all(r.status_code < 500 for r in responses), [r.text for r in responses]

    sensitive = [READER_PASSWORD, WRONG_PASSWORD, READER_USER, f"{postgres.host}:{postgres.port}"]

    for response in responses:
        rendered = response.text + json.dumps(dict(response.headers))
        for value in sensitive:
            assert value not in rendered, (response.request.url, value)

    assert captured_logs, "the log capture saw nothing; the test would prove nothing"
    for line in captured_logs:
        for value in sensitive:
            assert value not in line, (value, line[:300])

    rows: list[str] = []
    for table in ("data_sources", "schema_snapshots", "tables", "columns", "relationships"):
        rows += [
            record["row"]
            for record in await platform_db.fetch(
                "SELECT row_to_json(t)::text AS row FROM metadata." + table + " t"  # noqa: S608
            )
        ]
    assert rows
    for row in rows:
        for value in sensitive:
            assert value not in row, (value, row[:300])

    audit = json.dumps(identity.audit_events)
    for value in sensitive:
        assert value not in audit

    # Positive control: the credentials are exactly where they belong.
    stored = await secrets.read(f"tenants/{tenant}/datasources/{created['id']}")
    assert stored is not None
    assert stored["password"] == READER_PASSWORD
    assert stored["username"] == READER_USER
    data_source_row = await platform_db.fetchrow(
        "SELECT secret_ref FROM metadata.data_sources WHERE id = $1", uuid.UUID(created["id"])
    )
    assert (
        data_source_row["secret_ref"] == f"secret/data/tenants/{tenant}/datasources/{created['id']}"
    )
