"""Unit tests for LLM planner response parsing with mocked clients."""

from __future__ import annotations

from typing import Any

from agent.llm_planner import LLMPlanner, OpenAIChatClient
from agent.planner import ClarificationPlan, ErrorPlan, ToolCallPlan


class FakeClient(OpenAIChatClient):
    """Simple fake chat client returning a fixed completion payload."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response

    def create_chat_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str,
    ) -> dict[str, Any]:
        return self.response


def _response_with_tool_call(name: str, arguments: str) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "function": {
                                "name": name,
                                "arguments": arguments,
                            }
                        }
                    ],
                }
            }
        ]
    }


def test_llm_planner_parses_sum_tool_call(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "dev")

    planner = LLMPlanner(
        client=FakeClient(
            _response_with_tool_call(
                "finance_transactions_sum",
                '{"search": "cafe", "direction": "DEBIT_ONLY"}',
            )
        )
    )

    plan = planner.plan("Combien j'ai dépensé en café ?")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_transactions_sum"
    assert plan.payload == {"search": "cafe", "direction": "DEBIT_ONLY"}


def test_llm_planner_parses_search_tool_call(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "dev")

    planner = LLMPlanner(
        client=FakeClient(
            _response_with_tool_call(
                "finance_transactions_search",
                '{"search": "coffee", "limit": 10, "offset": 0}',
            )
        )
    )

    plan = planner.plan("Liste mes transactions café")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_transactions_search"
    assert plan.payload["search"] == "coffee"


def test_llm_planner_returns_validation_error_on_invalid_json(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "dev")

    planner = LLMPlanner(client=FakeClient(_response_with_tool_call("finance_transactions_sum", "{bad json")))

    plan = planner.plan("Total café")

    assert isinstance(plan, ErrorPlan)
    assert plan.tool_error.code.value == "VALIDATION_ERROR"
    assert "raw_arguments" in (plan.tool_error.details or {})


def test_llm_planner_returns_clarification_for_unknown_tool(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "dev")

    planner = LLMPlanner(
        client=FakeClient(_response_with_tool_call("finance_transactions_delete", '{"id": "tx_1"}'))
    )

    plan = planner.plan("Supprime cette transaction")

    assert isinstance(plan, ClarificationPlan)
    assert "rechercher des transactions" in plan.question


def test_llm_planner_returns_clarification_when_no_tool_call(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "dev")

    planner = LLMPlanner(
        client=FakeClient(
            {
                "choices": [
                    {
                        "message": {
                            "content": "Pouvez-vous préciser la période ?",
                            "tool_calls": [],
                        }
                    }
                ]
            }
        )
    )

    plan = planner.plan("Combien ai-je dépensé en café ?")

    assert isinstance(plan, ClarificationPlan)
    assert plan.question == "Pouvez-vous préciser la période ?"
