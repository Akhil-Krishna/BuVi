"""A run with several active data sources and none named picks the one the question is about.

Before this, more than one active source failed the run outright with "Choose which data source to
use" -- and the chat UI had no way to choose. The unit tests pin the scoring rule; these prove the
Flow uses it end to end and, more importantly, that it changes *who picks* and nothing about what
is allowed.
"""

from __future__ import annotations

import uuid

import pytest

from analytics_orchestrator.tests.conftest import LOGISTICS_TABLES, MESSAGE, Harness

pytestmark = pytest.mark.integration

#: Nothing in `CONTEXT_TABLES` (sales.orders / sales.regions) matches this; the logistics catalog does.
LOGISTICS_MESSAGE = "Show a chart of monthly shipments by warehouse"


async def completed_source(harness: Harness, who: object, run_id: str) -> tuple[str, list[str]]:
    result = (await harness.execute(who, run_id)).json()  # type: ignore[arg-type]
    assert result["status"] == "completed", result
    (stored,) = harness.services.artifacts.values()
    ref = stored["source_refs"][0]
    return ref["data_source_id"], ref["tables"]


async def test_the_question_picks_the_source_that_has_its_table(
    harness: Harness, tenant: uuid.UUID
) -> None:
    who = harness.services.add_user(tenant, {"client"})
    harness.services.add_data_source(tenant)  # sales.orders, sales.regions
    logistics = harness.services.add_data_source(tenant, LOGISTICS_TABLES)

    source, tables = await completed_source(
        harness, who, await harness.start_run(who, LOGISTICS_MESSAGE)
    )
    assert source == str(logistics) and tables == ["logistics.shipments"]


async def test_the_same_tenant_routes_a_different_question_to_the_other_source(
    harness: Harness, tenant: uuid.UUID
) -> None:
    """Not a fixed default: the choice follows the question."""
    who = harness.services.add_user(tenant, {"client"})
    sales = harness.services.add_data_source(tenant)
    harness.services.add_data_source(tenant, LOGISTICS_TABLES)

    source, tables = await completed_source(harness, who, await harness.start_run(who, MESSAGE))
    assert source == str(sales) and tables == ["sales.orders"]


async def test_an_explicit_data_source_is_never_overridden_by_routing(
    harness: Harness, tenant: uuid.UUID
) -> None:
    """The question is plainly about logistics, but the caller named the sales source."""
    who = harness.services.add_user(tenant, {"client"})
    sales = harness.services.add_data_source(tenant)
    harness.services.add_data_source(tenant, LOGISTICS_TABLES)

    source, _ = await completed_source(
        harness, who, await harness.start_run(who, LOGISTICS_MESSAGE, data_source_id=str(sales))
    )
    assert source == str(sales)


async def test_nothing_matching_in_any_source_says_so_instead_of_picking_one(
    harness: Harness, tenant: uuid.UUID
) -> None:
    who = harness.services.add_user(tenant, {"client"})
    harness.services.add_data_source(tenant)
    harness.services.add_data_source(tenant, LOGISTICS_TABLES)

    run_id = await harness.start_run(who, "Show a chart of quarterly zebra migration")
    result = (await harness.execute(who, run_id)).json()
    assert result["status"] == "failed" and result["error_code"] == "NO_RELEVANT_DATA"
    assert harness.services.query_calls == [] and harness.services.artifacts == {}


async def test_identical_sources_are_a_tie_and_still_ask(
    harness: Harness, tenant: uuid.UUID
) -> None:
    """Guessing between two databases that both fit would answer from the wrong one."""
    who = harness.services.add_user(tenant, {"client"})
    harness.services.add_data_source(tenant)
    harness.services.add_data_source(tenant)

    result = (await harness.execute(who, await harness.start_run(who))).json()
    assert result["error_code"] == "DATA_SOURCE_SELECTION_REQUIRED"
    assert harness.services.query_calls == []


async def test_routing_never_considers_another_tenants_source(
    harness: Harness, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    """The candidates are exactly what an explicit id would be accepted for: this tenant's own
    active sources. A foreign source that matches the question perfectly is invisible to it."""
    who = harness.services.add_user(tenant, {"client"})
    harness.services.add_data_source(tenant)  # sales
    harness.services.add_data_source(tenant)  # a second sales-shaped one: a tie between the two
    harness.services.add_data_source(other_tenant, LOGISTICS_TABLES)  # perfect match, not theirs

    result = (await harness.execute(who, await harness.start_run(who, LOGISTICS_MESSAGE))).json()
    assert result["status"] == "failed"
    assert result["error_code"] == "NO_RELEVANT_DATA"  # not a completed run on logistics
    assert harness.services.query_calls == [] and harness.services.artifacts == {}
