"""Unit tests for strict follow-up extraction and payload keys."""

from __future__ import annotations

from agent.memory import QueryMemory, followup_plan_from_message


def _memory_for_sum() -> QueryMemory:
    return QueryMemory(
        date_range={"start_date": "2026-01-01", "end_date": "2026-01-31"},
        last_tool_name="finance_releves_sum",
        last_intent="sum",
        filters={"direction": "DEBIT_ONLY"},
    )


def test_followup_sum_uses_categorie_payload_key() -> None:
    plan = followup_plan_from_message(
        "Et en alimentation ?",
        _memory_for_sum(),
        known_categories=["Logement", "Alimentation"],
    )

    assert plan is not None
    assert plan.tool_name == "finance_releves_sum"
    assert plan.payload == {
        "direction": "DEBIT_ONLY",
        "date_range": {"start_date": "2026-01-01", "end_date": "2026-01-31"},
        "categorie": "Alimentation",
    }


def test_followup_sum_requires_known_categories_for_category_focus() -> None:
    plan = followup_plan_from_message("Et en alimentation ?", _memory_for_sum())

    assert plan is None


def test_followup_sum_known_category_keeps_exact_casing_and_accents() -> None:
    plan = followup_plan_from_message(
        "Et en santé ?",
        _memory_for_sum(),
        known_categories=["Santé", "Logement"],
    )

    assert plan is not None
    assert plan.payload["categorie"] == "Santé"


def test_followup_list_categories_is_not_hijacked() -> None:
    plan = followup_plan_from_message("Liste mes catégories", _memory_for_sum())

    assert plan is None


def test_followup_known_category_fallback_uses_categorie_key() -> None:
    plan = followup_plan_from_message(
        "Coop",
        QueryMemory(
            date_range={"start_date": "2026-01-01", "end_date": "2026-01-31"},
            last_tool_name="finance_releves_aggregate",
            last_intent="aggregate",
            filters={"group_by": "categorie", "direction": "DEBIT_ONLY"},
        ),
        known_categories=["Logement", "Alimentation"],
    )

    assert plan is None

    category_plan = followup_plan_from_message(
        "Et logement ?",
        QueryMemory(
            date_range={"start_date": "2026-01-01", "end_date": "2026-01-31"},
            last_tool_name="finance_releves_aggregate",
            last_intent="aggregate",
            filters={"group_by": "categorie", "direction": "DEBIT_ONLY"},
        ),
        known_categories=["Logement", "Alimentation"],
    )

    assert category_plan is not None
    assert category_plan.payload["categorie"] == "Logement"
    assert "category" not in category_plan.payload


def test_followup_sum_merchant_focus_uses_merchant_filter() -> None:
    plan = followup_plan_from_message("Et chez Coop ?", _memory_for_sum())

    assert plan is not None
    assert plan.tool_name == "finance_releves_sum"
    assert plan.payload == {
        "direction": "DEBIT_ONLY",
        "merchant": "coop",
        "date_range": {"start_date": "2026-01-01", "end_date": "2026-01-31"},
    }
