"""Tests for memory injection edge-cases on follow-up sums."""

from __future__ import annotations

from agent.memory import QueryMemory, apply_memory_to_plan
from agent.planner import ToolCallPlan


def test_apply_memory_does_not_inject_merchant_when_sum_has_categorie() -> None:
    memory = QueryMemory(
        date_range={"start_date": "2026-02-01", "end_date": "2026-02-28"},
        last_tool_name="finance_releves_search",
        last_intent="search",
        filters={"merchant": "pizza", "direction": "DEBIT_ONLY"},
    )
    plan = ToolCallPlan(
        tool_name="finance_releves_sum",
        payload={
            "categorie": "Loisir",
            "date_range": {"start_date": "2026-02-01", "end_date": "2026-02-28"},
        },
        user_reply="OK.",
    )

    updated_plan, _ = apply_memory_to_plan("Et en loisir ?", plan, memory)

    assert updated_plan.payload == {
        "categorie": "Loisir",
        "date_range": {"start_date": "2026-02-01", "end_date": "2026-02-28"},
        "direction": "DEBIT_ONLY",
    }
    assert "merchant" not in updated_plan.payload


def test_apply_memory_does_not_inject_search_when_sum_has_categorie() -> None:
    memory = QueryMemory(
        date_range={"start_date": "2026-02-01", "end_date": "2026-02-28"},
        last_tool_name="finance_releves_search",
        last_intent="search",
        filters={"search": "pizza", "direction": "DEBIT_ONLY"},
    )
    plan = ToolCallPlan(
        tool_name="finance_releves_sum",
        payload={
            "categorie": "Loisir",
            "date_range": {"start_date": "2026-02-01", "end_date": "2026-02-28"},
        },
        user_reply="OK.",
    )

    updated_plan, _ = apply_memory_to_plan("Et en loisir ?", plan, memory)

    assert updated_plan.payload == {
        "categorie": "Loisir",
        "date_range": {"start_date": "2026-02-01", "end_date": "2026-02-28"},
        "direction": "DEBIT_ONLY",
    }
    assert "search" not in updated_plan.payload


def test_apply_memory_keeps_explicit_search_when_sum_has_categorie() -> None:
    memory = QueryMemory(
        date_range={"start_date": "2026-02-01", "end_date": "2026-02-28"},
        last_tool_name="finance_releves_search",
        last_intent="search",
        filters={"search": "pizza", "direction": "DEBIT_ONLY"},
    )
    plan = ToolCallPlan(
        tool_name="finance_releves_sum",
        payload={
            "categorie": "Loisir",
            "search": "cinema",
            "date_range": {"start_date": "2026-02-01", "end_date": "2026-02-28"},
        },
        user_reply="OK.",
    )

    updated_plan, _ = apply_memory_to_plan("Et en loisir ?", plan, memory)

    assert updated_plan.payload == {
        "categorie": "Loisir",
        "search": "cinema",
        "date_range": {"start_date": "2026-02-01", "end_date": "2026-02-28"},
        "direction": "DEBIT_ONLY",
    }
