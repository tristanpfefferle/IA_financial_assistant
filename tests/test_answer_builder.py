"""Tests for building final user replies from tool results."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from agent.answer_builder import build_final_reply
from agent.planner import ToolCallPlan
from shared.models import (
    RelevesAggregateGroup,
    RelevesAggregateRequest,
    RelevesAggregateResult,
    RelevesDirection,
    RelevesFilters,
    RelevesGroupBy,
    RelevesSumResult,
    ToolError,
    ToolErrorCode,
)


DEBIT_ONLY_NOTE = "Certaines catégories peuvent être exclues des totaux (ex: Transfert interne)."


def test_build_final_reply_with_releves_sum_result() -> None:
    plan = ToolCallPlan(tool_name="finance_releves_sum", payload={}, user_reply="OK")
    result = RelevesSumResult(
        total=Decimal("-123.45"),
        count=4,
        average=Decimal("30.86"),
        currency="EUR",
        filters=RelevesFilters(profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), direction=RelevesDirection.DEBIT_ONLY),
    )

    reply = build_final_reply(plan=plan, tool_result=result)

    assert "123.45" in reply
    assert "-123.45" not in reply
    assert "EUR" in reply
    assert "4" in reply
    assert DEBIT_ONLY_NOTE in reply


def test_build_final_reply_with_tool_error() -> None:
    plan = ToolCallPlan(tool_name="finance_releves_sum", payload={}, user_reply="OK")
    error = ToolError(code=ToolErrorCode.BACKEND_ERROR, message="Service indisponible")

    reply = build_final_reply(plan=plan, tool_result=error)

    assert "Erreur" in reply
    assert "Service indisponible" in reply


def test_build_final_reply_with_releves_sum_all_is_neutral() -> None:
    plan = ToolCallPlan(tool_name="finance_releves_sum", payload={}, user_reply="OK")
    result = RelevesSumResult(
        total=Decimal("100.00"),
        count=2,
        average=Decimal("50.00"),
        currency=None,
        filters=RelevesFilters(profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), direction=RelevesDirection.ALL),
    )

    reply = build_final_reply(plan=plan, tool_result=result)

    assert "Total net (revenus + dépenses)" in reply
    assert "100.00 sur 2 opération(s)." in reply
    assert DEBIT_ONLY_NOTE not in reply


def test_build_final_reply_with_releves_aggregate_result_sorted_and_limited() -> None:
    plan = ToolCallPlan(tool_name="finance_releves_aggregate", payload={}, user_reply="OK")
    groups = {
        f"g{i}": RelevesAggregateGroup(total=Decimal(str(-i * 10)), count=i)
        for i in range(1, 13)
    }
    result = RelevesAggregateResult(
        group_by=RelevesGroupBy.CATEGORIE,
        groups=groups,
        currency="CHF",
        filters=RelevesAggregateRequest(
            profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            group_by=RelevesGroupBy.CATEGORIE,
            direction=RelevesDirection.DEBIT_ONLY,
        ),
    )

    reply = build_final_reply(plan=plan, tool_result=result)

    assert "par categorie" in reply or "par catégorie" in reply
    assert "g12" in reply
    assert "- g1:" not in reply
    assert "Autres" in reply
    assert "-120.00" not in reply
    assert DEBIT_ONLY_NOTE in reply
