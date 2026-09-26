"""Which data source a question is about, when the caller did not say (Phase C1 follow-up).

The rule is deliberately biased toward asking: a wrong guess answers from the wrong database and
the chart looks perfectly plausible, while a wrong refusal costs one click. These tests pin that
bias, not just the happy path.
"""

from __future__ import annotations

from analytics_orchestrator.domain.policies.context_policy import (
    SOURCE_MARGIN,
    choose_data_source,
    request_terms,
    table_score,
)
from analytics_orchestrator.domain.value_objects.run_state import ContextColumn, ContextTable


def table(name: str, *columns: str, description: str | None = None) -> ContextTable:
    return ContextTable(
        schema_name="s",
        table_name=name,
        description=description,
        columns=[ContextColumn(name=c, data_type="text") for c in columns],
    )


def terms(message: str) -> list[str]:
    return request_terms(None, message)


def test_a_name_hit_is_worth_three_column_hits() -> None:
    term_set = set(terms("shipments"))
    assert table_score(table("shipments"), term_set) == 3
    assert table_score(table("other", "shipments"), term_set) == 1


def test_the_source_with_the_table_the_question_is_about_wins() -> None:
    choice = choose_data_source(
        [
            ("warehouse", [table("shipments", "warehouse_id"), table("carriers")]),
            ("finance", [table("invoices", "amount")]),
        ],
        terms("monthly shipments by warehouse"),
    )
    assert choice.source_id == "warehouse" and choice.reason is None


def test_a_source_scores_as_its_best_table_not_its_table_count() -> None:
    """Twenty weak tables must not out-score one strong one."""
    weak = [table(f"t{i}", "shipments") for i in range(20)]  # 1 point each, never summed
    choice = choose_data_source(
        [("many_weak", weak), ("one_strong", [table("shipments")])],
        terms("shipments"),
    )
    assert choice.source_id == "one_strong"


def test_a_tie_asks_instead_of_guessing() -> None:
    same = [table("orders", "amount")]
    choice = choose_data_source([("a", same), ("b", same)], terms("orders amount"))
    assert choice.source_id is None and choice.reason == "ambiguous"


def test_a_lead_below_the_margin_still_asks() -> None:
    """One stray column word is not evidence a question is about one database over another."""
    lead = SOURCE_MARGIN - 1
    assert lead == 1  # a single column word
    choice = choose_data_source(
        [("a", [table("x", "shipments")]), ("b", [table("y")])], terms("shipments")
    )
    assert choice.source_id is None and choice.reason == "ambiguous"


def test_a_lead_of_exactly_the_margin_is_enough() -> None:
    choice = choose_data_source(
        [("a", [table("x", "shipments", "carrier")]), ("b", [table("y")])],
        terms("shipments carrier"),
    )
    assert choice.source_id == "a"


def test_nothing_matching_anywhere_is_reported_as_no_match_not_ambiguity() -> None:
    choice = choose_data_source(
        [("a", [table("orders")]), ("b", [table("invoices")])], terms("zebra migration")
    )
    assert choice.source_id is None and choice.reason == "no_match"


def test_a_lone_candidate_is_used_even_at_zero_score() -> None:
    """Same as a tenant with one source: `rank_tables` falls back to every table and the model
    still gets to plan, rather than the run refusing."""
    choice = choose_data_source([("only", [table("orders")])], terms("zebra migration"))
    assert choice.source_id == "only"


def test_no_candidates_is_no_match() -> None:
    assert choose_data_source([], terms("anything")).reason == "no_match"


def test_the_models_own_reading_of_the_request_widens_the_terms() -> None:
    """Routing runs after the intent step, so `request_terms` gets the model's metrics and
    dimensions, not just the user's words. 'store' never appears in the message."""
    from analytics_orchestrator.domain.value_objects.agent_outputs import AnalyticsRequest

    request = AnalyticsRequest(
        intent="visualization", title="Sales", metrics=["revenue"], dimensions=["store"]
    )
    assert "store" not in terms("how are we doing")
    assert "store" in request_terms(request, "how are we doing")


def test_a_singular_question_matches_a_plural_table_name() -> None:
    """The bug this guards: 'store' against a table called `stores` scored zero, because only an
    exact word matched -- so the strongest signal there is (a table-name hit) was thrown away."""
    assert table_score(table("stores"), set(terms("sales by store"))) == 3
    assert table_score(table("stores"), set(terms("sales by stores"))) == 3
    assert table_score(table("categories"), set(terms("by category"))) == 3  # -ies -> -y


def test_stemming_is_symmetric_so_odd_stems_still_match_themselves() -> None:
    """`status` stems to `statu` on both sides. What matters is consistency, not correctness."""
    assert table_score(table("orders", "status"), set(terms("order status"))) == 3 + 1


def test_words_that_only_look_plural_are_left_alone() -> None:
    """`ss` endings (address, class) and short words are not stripped."""
    assert terms("address class") == ["address", "class"]
    assert "is" in terms("is") and "as" in terms("as")
