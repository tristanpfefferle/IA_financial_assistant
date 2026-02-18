from __future__ import annotations

from agent.loop import AgentLoop
from agent.memory import QueryMemory
from agent.planner import ToolCallPlan


def _plan(*, payload: dict[str, object], meta: dict[str, object] | None = None) -> ToolCallPlan:
    return ToolCallPlan(
        tool_name="finance_releves_sum",
        payload=payload,
        user_reply="OK.",
        meta=meta or {},
    )


def test_confidence_followup_short_without_context_is_low() -> None:
    plan = _plan(
        payload={
            "direction": "DEBIT_ONLY",
            "date_range": {"start_date": "2026-01-01", "end_date": "2026-01-31"},
        }
    )

    updated = AgentLoop._with_confidence_meta("ok", plan, query_memory=None)

    assert updated.meta["confidence"] == "low"
    assert "period_missing_in_message" in updated.meta["confidence_reasons"]


def test_confidence_period_injected_from_memory_is_low() -> None:
    memory = QueryMemory(
        date_range={"start_date": "2025-12-01", "end_date": "2025-12-31"},
        last_tool_name="finance_releves_sum",
    )
    plan = _plan(
        payload={
            "direction": "DEBIT_ONLY",
            "date_range": {"start_date": "2025-12-01", "end_date": "2025-12-31"},
        },
        meta={"followup_from_memory": True, "memory_reason": "period_from_memory"},
    )

    updated = AgentLoop._with_confidence_meta("et en logement", plan, query_memory=memory)

    assert updated.meta["confidence"] == "low"
    assert "period_injected_from_memory" in updated.meta["confidence_reasons"]


def test_confidence_category_inferred_is_medium() -> None:
    plan = _plan(
        payload={
            "direction": "DEBIT_ONLY",
            "categorie": "Loisir",
            "date_range": {"start_date": "2026-01-01", "end_date": "2026-01-31"},
        }
    )

    updated = AgentLoop._with_confidence_meta(
        "Quel est le total des dépenses en janvier 2026 ?",
        plan,
        query_memory=None,
    )

    assert updated.meta["confidence"] == "medium"
    assert "category_inferred" in updated.meta["confidence_reasons"]


def test_confidence_explicit_message_is_high() -> None:
    plan = _plan(
        payload={
            "direction": "DEBIT_ONLY",
            "merchant": "Coop",
            "date_range": {"start_date": "2026-01-01", "end_date": "2026-01-31"},
        }
    )

    updated = AgentLoop._with_confidence_meta(
        "Quel est le total des dépenses chez Coop en janvier 2026 ?",
        plan,
        query_memory=None,
    )

    assert updated.meta["confidence"] == "high"
    assert "explicit_intent" in updated.meta["confidence_reasons"]
    assert "explicit_period" in updated.meta["confidence_reasons"]
    assert "explicit_filter" in updated.meta["confidence_reasons"]


def test_confidence_followup_with_memory_stays_medium() -> None:
    memory = QueryMemory(
        date_range={"start_date": "2026-01-01", "end_date": "2026-01-31"},
        last_tool_name="finance_releves_sum",
    )
    plan = _plan(
        payload={
            "direction": "DEBIT_ONLY",
            "categorie": "logement",
            "date_range": {"start_date": "2026-01-01", "end_date": "2026-01-31"},
        },
        meta={"followup_from_memory": True},
    )

    updated = AgentLoop._with_confidence_meta("Ok et en logement ?", plan, query_memory=memory)

    assert updated.meta["confidence"] == "medium"
    assert "period_missing_in_message" in updated.meta["confidence_reasons"]


def test_confidence_explicit_merchant_conflict_is_low() -> None:
    memory = QueryMemory(
        date_range={"start_date": "2026-01-01", "end_date": "2026-01-31"},
        last_tool_name="finance_releves_sum",
    )
    plan = _plan(
        payload={
            "direction": "DEBIT_ONLY",
            "merchant": "coop",
            "date_range": {"start_date": "2026-01-01", "end_date": "2026-01-31"},
        },
        meta={"followup_from_memory": True},
    )

    updated = AgentLoop._with_confidence_meta("Et chez Migros ?", plan, query_memory=memory)

    assert updated.meta["confidence"] == "low"
    assert "merchant_conflict" in updated.meta["confidence_reasons"]


def test_confidence_period_conflict_is_low() -> None:
    memory = QueryMemory(
        date_range={"start_date": "2025-12-01", "end_date": "2025-12-31"},
        last_tool_name="finance_releves_sum",
    )
    plan = _plan(
        payload={
            "direction": "DEBIT_ONLY",
            "date_range": {"start_date": "2025-12-01", "end_date": "2025-12-31"},
        },
        meta={"followup_from_memory": True},
    )

    updated = AgentLoop._with_confidence_meta("et en janvier 2026", plan, query_memory=memory)

    assert updated.meta["confidence"] == "low"
    assert "period_conflict" in updated.meta["confidence_reasons"]


def test_confidence_explicit_merchant_conflict_multiword_is_low() -> None:
    memory = QueryMemory(
        date_range={"start_date": "2026-01-01", "end_date": "2026-01-31"},
        last_tool_name="finance_releves_sum",
    )
    plan = _plan(
        payload={
            "direction": "DEBIT_ONLY",
            "merchant": "coop",
            "date_range": {"start_date": "2026-01-01", "end_date": "2026-01-31"},
        },
        meta={"followup_from_memory": True},
    )

    updated = AgentLoop._with_confidence_meta("Et chez Migros Online ?", plan, query_memory=memory)

    assert updated.meta["confidence"] == "low"
    assert "merchant_conflict" in updated.meta["confidence_reasons"]
