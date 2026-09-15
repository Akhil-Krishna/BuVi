"""Section 25's unsafe-SQL corpus against the Section 13 allow-list -- written before execution.

Phase A4 DoD: "the unsafe-SQL corpus (DDL/DML/multi-statement/file functions/etc.) is 100%
rejected". Every corpus entry is asserted individually (with its expected reason) and in
aggregate (the rejection rate), for every purpose. The safe corpus proves the allow-list is
usable for real analytics, and pins what a passing query turns into.
"""

from __future__ import annotations

import pytest
import sqlglot
from sqlglot import exp

from query_gateway.domain.policies.sql_validator import MESSAGES, RejectionReason, SqlValidator
from query_gateway.domain.value_objects.policy import Purpose
from query_gateway.tests.unit.corpus import (
    AGENT_ONLY_UNSAFE,
    AMBIGUOUS_POLICY,
    POLICY,
    SAFE,
    UNSAFE,
)

pytestmark = [pytest.mark.unit, pytest.mark.security]

VALIDATOR = SqlValidator(max_length=50_000)
ALL_PURPOSES = [Purpose.SQL_EDITOR, Purpose.ANALYTICS_RUN]


@pytest.mark.parametrize("purpose", ALL_PURPOSES)
@pytest.mark.parametrize(("name", "sql", "reason"), UNSAFE, ids=[c[0] for c in UNSAFE])
def test_unsafe_corpus_entry_is_rejected(
    name: str, sql: str, reason: RejectionReason, purpose: Purpose
) -> None:
    outcome = VALIDATOR.validate(sql, POLICY, purpose=purpose)
    assert not outcome.ok, f"{name} was accepted: {outcome.sql}"
    assert outcome.sql is None
    assert outcome.reason is reason, f"{name}: {outcome.reason} (detail={outcome.detail})"


@pytest.mark.parametrize(
    ("name", "sql", "reason"), AGENT_ONLY_UNSAFE, ids=[c[0] for c in AGENT_ONLY_UNSAFE]
)
def test_agent_path_rejects_pii_and_invisible_tables(
    name: str, sql: str, reason: RejectionReason
) -> None:
    outcome = VALIDATOR.validate(sql, POLICY, purpose=Purpose.ANALYTICS_RUN)
    assert (outcome.ok, outcome.reason) == (False, reason), (name, outcome)
    assert outcome.sql is None


def test_one_hundred_percent_of_the_unsafe_corpus_is_rejected() -> None:
    """The DoD metric, stated as a number."""
    total = rejected = 0
    for purpose in ALL_PURPOSES:
        for _name, sql, _reason in UNSAFE:
            total += 1
            rejected += not VALIDATOR.validate(sql, POLICY, purpose=purpose).ok
    for _name, sql, _reason in AGENT_ONLY_UNSAFE:
        total += 1
        rejected += not VALIDATOR.validate(sql, POLICY, purpose=Purpose.ANALYTICS_RUN).ok
    assert total >= 250
    assert rejected / total == 1.0


def test_corpus_covers_every_section_13_rule() -> None:
    reasons = {reason for _n, _s, reason in UNSAFE + AGENT_ONLY_UNSAFE}
    assert reasons == set(RejectionReason) - {
        RejectionReason.AMBIGUOUS_TABLE,
        RejectionReason.TOO_COMPLEX,
    }


@pytest.mark.parametrize("purpose", ALL_PURPOSES)
@pytest.mark.parametrize(("name", "sql"), SAFE, ids=[c[0] for c in SAFE])
def test_safe_corpus_passes_and_is_regenerated(name: str, sql: str, purpose: Purpose) -> None:
    outcome = VALIDATOR.validate(sql, POLICY, purpose=purpose)
    assert outcome.ok, f"{name}: {outcome.reason} {outcome.detail}"
    assert outcome.sql is not None
    tree = sqlglot.parse_one(outcome.sql, read="postgres")
    # Every real table reference is fully qualified and quoted; nothing but a SELECT runs.
    for table in tree.find_all(exp.Table):
        if table.db:
            assert table.args["db"].quoted and table.this.quoted
    assert "--" not in outcome.sql and "/*" not in outcome.sql
    assert outcome.sql_hash and len(outcome.sql_hash) == 64


def test_pii_columns_are_allowed_on_the_sql_editor_path() -> None:
    """Section 12's PII exclusion governs agent context; the developer SQL editor is separate."""
    for sql in ("SELECT email FROM sales.customers", "SELECT * FROM sales.customers"):
        assert VALIDATOR.validate(sql, POLICY, purpose=Purpose.SQL_EDITOR).ok
    assert VALIDATOR.validate(
        "SELECT note FROM sales.internal_notes", POLICY, purpose=Purpose.SQL_EDITOR
    ).ok


def test_star_is_expanded_to_catalogued_columns() -> None:
    outcome = VALIDATOR.validate(
        "SELECT * FROM sales.regions", POLICY, purpose=Purpose.ANALYTICS_RUN
    )
    assert (
        outcome.sql
        == 'SELECT "regions"."id" AS "id", "regions"."name" AS "name" FROM "sales"."regions" AS "regions"'
    )
    assert outcome.tables == ("sales.regions",)


def test_unqualified_table_is_resolved_into_its_allowed_schema() -> None:
    outcome = VALIDATOR.validate("SELECT id FROM orders", POLICY, purpose=Purpose.SQL_EDITOR)
    assert outcome.ok and '"sales"."orders"' in (outcome.sql or "")


def test_ambiguous_unqualified_table_is_rejected() -> None:
    outcome = VALIDATOR.validate(
        "SELECT id FROM orders", AMBIGUOUS_POLICY, purpose=Purpose.SQL_EDITOR
    )
    assert (outcome.ok, outcome.reason) == (False, RejectionReason.AMBIGUOUS_TABLE)
    assert VALIDATOR.validate(
        "SELECT id FROM finance.orders", AMBIGUOUS_POLICY, purpose=Purpose.SQL_EDITOR
    ).ok


def test_quoted_identifiers_keep_case_so_a_differently_cased_table_is_unknown() -> None:
    outcome = VALIDATOR.validate(
        'SELECT id FROM sales."Orders"', POLICY, purpose=Purpose.SQL_EDITOR
    )
    assert (outcome.ok, outcome.reason) == (False, RejectionReason.TABLE_NOT_ALLOWED)


def test_too_complex_trees_are_rejected() -> None:
    small = SqlValidator(max_nodes=50)
    sql = "SELECT " + " + ".join(["amount"] * 60) + " FROM sales.orders"  # noqa: S608
    assert (
        small.validate(sql, POLICY, purpose=Purpose.SQL_EDITOR).reason
        is RejectionReason.TOO_COMPLEX
    )
    deep = "SELECT " + "(" * 3000 + "1" + ")" * 3000
    outcome = VALIDATOR.validate(deep, POLICY, purpose=Purpose.SQL_EDITOR)
    assert not outcome.ok and outcome.reason in (
        RejectionReason.TOO_COMPLEX,
        RejectionReason.PARSE_ERROR,
    )


def test_rejection_details_are_identifiers_never_parser_text() -> None:
    cases = {
        "SELECT pg_read_file('/etc/passwd')": "pg_read_file",
        "DELETE FROM sales.orders": "DELETE",
        "SELECT * FROM private.salaries": "private.salaries",
        "SELECT password FROM sales.customers": "password",
    }
    for sql, detail in cases.items():
        outcome = VALIDATOR.validate(sql, POLICY, purpose=Purpose.SQL_EDITOR)
        assert outcome.detail == detail
        assert "/etc/passwd" not in (outcome.detail or "")
        assert outcome.message == MESSAGES[outcome.reason]  # type: ignore[index]
    garbage = VALIDATOR.validate("SELEC id FRM sales.orders", POLICY, purpose=Purpose.SQL_EDITOR)
    assert garbage.detail is None
    long_identifier = "x" * 500
    outcome = VALIDATOR.validate(f"SELECT {long_identifier}()", POLICY, purpose=Purpose.SQL_EDITOR)
    assert outcome.detail is not None and len(outcome.detail) <= 128


def test_audit_record_shape() -> None:
    ok = VALIDATOR.validate(
        "SELECT id FROM sales.orders", POLICY, purpose=Purpose.SQL_EDITOR
    ).to_audit()
    assert ok == {
        "valid": True,
        "reason": None,
        "detail": None,
        "tables": ["sales.orders"],
        "normalized_sql_sha256": ok["normalized_sql_sha256"],
    }
    bad = VALIDATOR.validate(
        "DROP TABLE sales.orders", POLICY, purpose=Purpose.SQL_EDITOR
    ).to_audit()
    assert (
        bad["valid"] is False
        and bad["reason"] == "WRITE_OPERATION"
        and bad["normalized_sql_sha256"] is None
    )
