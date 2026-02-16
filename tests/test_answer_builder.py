"""Tests for building final user replies from tool results."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from agent.answer_builder import build_final_reply
from agent.planner import ToolCallPlan
from shared.models import RelevesDirection, RelevesFilters, RelevesSumResult, ToolError, ToolErrorCode


def test_build_final_reply_with_releves_sum_result() -> None:
    plan = ToolCallPlan(tool_name="finance_releves_sum", payload={}, user_reply="OK")
    result = RelevesSumResult(
        total=Decimal("123.45"),
        count=4,
        average=Decimal("30.86"),
        currency="EUR",
        filters=RelevesFilters(profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), direction=RelevesDirection.DEBIT_ONLY),
    )

    reply = build_final_reply(plan=plan, tool_result=result)

    assert "123.45" in reply
    assert "EUR" in reply
    assert "4" in reply


def test_build_final_reply_with_tool_error() -> None:
    plan = ToolCallPlan(tool_name="finance_releves_sum", payload={}, user_reply="OK")
    error = ToolError(code=ToolErrorCode.BACKEND_ERROR, message="Service indisponible")

    reply = build_final_reply(plan=plan, tool_result=error)

    assert "Erreur" in reply
    assert "Service indisponible" in reply
