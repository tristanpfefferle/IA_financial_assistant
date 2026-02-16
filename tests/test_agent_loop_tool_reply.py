"""Tests for final agent replies after tool execution."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import agent.loop as loop_module
from agent.loop import AgentLoop
from agent.planner import ToolCallPlan
from shared.models import RelevesSumResult


class _FakeRouter:
    def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None) -> RelevesSumResult:
        assert tool_name == "finance_releves_sum"
        assert payload["direction"] == "DEBIT_ONLY"
        return RelevesSumResult(total=Decimal("250.00"), count=5, average=Decimal("50.00"), currency="EUR")


def test_tool_call_reply_contains_final_amount(monkeypatch) -> None:
    monkeypatch.setattr(
        loop_module,
        "plan_from_message",
        lambda _message, llm_planner=None, active_task=None: ToolCallPlan(
            tool_name="finance_releves_sum",
            payload={"direction": "DEBIT_ONLY"},
            user_reply="OK, je calcule…",
        ),
    )

    agent_loop = AgentLoop(tool_router=_FakeRouter())
    response = agent_loop.handle_user_message("total dépenses janvier", profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))

    assert "250.00" in response.reply
    assert "OK, je calcule" not in response.reply
    assert response.tool_result is not None
