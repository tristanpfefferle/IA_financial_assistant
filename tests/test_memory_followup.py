"""Unit tests for strict follow-up extraction and payload keys."""

from __future__ import annotations

from agent.memory import (
    QueryMemory,
    apply_memory_to_plan,
    followup_plan_from_message,
    is_followup_message,
)
from agent.planner import ToolCallPlan


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


def test_is_followup_message_is_strict_for_full_intent_queries() -> None:
    assert is_followup_message("Dépenses totales en janvier 2026") is False
    assert is_followup_message("Transactions Migros en janvier 2026") is False
    assert is_followup_message("Et en logement ?") is True
    assert is_followup_message("Coop") is True


def test_apply_memory_to_plan_does_not_inject_merchant_on_non_followup_intent() -> None:
    memory = QueryMemory(
        date_range={"start_date": "2026-01-01", "end_date": "2026-01-31"},
        last_tool_name="finance_releves_sum",
        last_intent="sum",
        filters={"merchant": "coop"},
    )
    plan = ToolCallPlan(
        tool_name="finance_releves_sum",
        payload={
            "direction": "DEBIT_ONLY",
            "date_range": {"start_date": "2026-01-01", "end_date": "2026-01-31"},
        },
        user_reply="OK.",
    )

    updated_plan, _ = apply_memory_to_plan("Dépenses totales en janvier 2026", plan, memory)

    assert "merchant" not in updated_plan.payload


def test_followup_period_change_reuses_filters_and_replaces_only_period() -> None:
    memory = QueryMemory(
        date_range={"start_date": "2026-12-01", "end_date": "2026-12-31"},
        last_tool_name="finance_releves_sum",
        last_intent="sum",
        filters={"direction": "DEBIT_ONLY", "categorie": "Alimentation"},
    )

    plan = followup_plan_from_message("Et en janvier ?", memory)

    assert plan is not None
    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_releves_sum"
    assert plan.payload["date_range"] == {
        "start_date": "2026-01-01",
        "end_date": "2026-01-31",
    }
    assert plan.payload["categorie"] == "Alimentation"
    assert plan.payload["categorie"] != "Et en janvier"


def test_followup_period_change_without_year_and_without_memory_date_range_requests_clarification() -> None:
    memory = QueryMemory(
        last_tool_name="finance_releves_sum",
        last_intent="sum",
        filters={"direction": "DEBIT_ONLY", "categorie": "Alimentation"},
    )

    plan = followup_plan_from_message("Et en janvier ?", memory)

    assert plan is not None
    assert getattr(plan, "question", None) is not None


def test_extract_memory_from_plan_drops_period_like_category_phrase() -> None:
    from agent.memory import extract_memory_from_plan

    memory = extract_memory_from_plan(
        "finance_releves_sum",
        {
            "direction": "DEBIT_ONLY",
            "categorie": "Et en décembre 2025",
            "date_range": {"start_date": "2025-12-01", "end_date": "2025-12-31"},
        },
    )

    assert memory is not None
    assert "categorie" not in memory.filters
