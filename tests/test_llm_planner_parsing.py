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
                '{"merchant": "cafe", "direction": "DEBIT_ONLY"}',
            )
        )
    )

    plan = planner.plan("Combien j'ai dépensé en café ?")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_releves_sum"
    assert plan.payload == {"merchant": "cafe", "direction": "DEBIT_ONLY"}


def test_llm_planner_parses_search_tool_call(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "dev")

    planner = LLMPlanner(
        client=FakeClient(
            _response_with_tool_call(
                "finance_transactions_search",
                '{"merchant": "coffee", "limit": 10, "offset": 0}',
            )
        )
    )

    plan = planner.plan("Liste mes transactions café")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_releves_search"
    assert plan.payload["merchant"] == "coffee"


def test_llm_planner_returns_validation_error_on_invalid_json(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "dev")

    planner = LLMPlanner(client=FakeClient(_response_with_tool_call("finance_transactions_sum", "{bad json")))

    plan = planner.plan("Total café")

    assert isinstance(plan, ErrorPlan)
    assert plan.tool_error.code.value == "VALIDATION_ERROR"
    assert "raw_arguments" in (plan.tool_error.details or {})


def test_llm_planner_returns_unknown_tool_error_for_unknown_tool(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "dev")

    planner = LLMPlanner(
        client=FakeClient(_response_with_tool_call("finance_transactions_delete", '{"id": "tx_1"}'))
    )

    plan = planner.plan("Supprime cette transaction")

    assert isinstance(plan, ErrorPlan)
    assert plan.tool_error.code.value == "UNKNOWN_TOOL"


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


def test_llm_planner_parses_releves_sum_tool_call(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "dev")

    planner = LLMPlanner(
        client=FakeClient(
            _response_with_tool_call(
                "finance_releves_sum",
                '{"direction": "DEBIT_ONLY"}',
            )
        )
    )

    plan = planner.plan("Total de mes dépenses")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_releves_sum"
    assert plan.payload == {"direction": "DEBIT_ONLY"}
    assert plan.user_reply == ""


def test_llm_planner_parses_releves_aggregate_tool_call(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "dev")

    planner = LLMPlanner(
        client=FakeClient(
            _response_with_tool_call(
                "finance_releves_aggregate",
                '{"group_by": "categorie", "direction": "DEBIT_ONLY"}',
            )
        )
    )

    plan = planner.plan("Agrège mes dépenses par catégorie")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_releves_aggregate"
    assert plan.payload == {"group_by": "categorie", "direction": "DEBIT_ONLY"}
    assert plan.user_reply == ""




def test_llm_planner_parses_bank_accounts_list_tool_call(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "dev")

    planner = LLMPlanner(
        client=FakeClient(
            _response_with_tool_call(
                "finance_bank_accounts_list",
                '{}',
            )
        )
    )

    plan = planner.plan("Montre moi mes comptes bancaires")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_bank_accounts_list"
    assert plan.payload == {}
    assert plan.user_reply == ""


def test_llm_planner_parses_categories_update_tool_call(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "dev")

    planner = LLMPlanner(
        client=FakeClient(
            _response_with_tool_call(
                "finance_categories_update",
                '{"category_id": "44444444-4444-4444-4444-444444444444", "exclude_from_totals": true}',
            )
        )
    )

    plan = planner.plan("Exclus ma catégorie des totaux")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_categories_update"
    assert plan.payload["exclude_from_totals"] is True






def test_llm_planner_messages_include_category_delete_and_city_few_shots() -> None:
    messages = LLMPlanner._messages("test")

    contents = [message["content"] for message in messages if message["role"] == "system"]

    assert any("supprimer ma catégorie restaurants" in content.casefold() for content in contents)
    assert any("mettre choëx comme ville" in content.casefold() for content in contents)
    assert any("ne génère jamais 'ville'/'pays'" in content.casefold() for content in contents)


def test_llm_planner_parses_categories_delete_from_french_delete_prompt(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "dev")

    planner = LLMPlanner(
        client=FakeClient(
            _response_with_tool_call(
                "finance_categories_delete",
                '{"category_name": "restaurants"}',
            )
        )
    )

    plan = planner.plan("Peux-tu supprimer ma catégorie restaurants stp ?")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_categories_delete"
    assert plan.payload == {"category_name": "restaurants"}


def test_llm_planner_parses_city_update_with_accented_value(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "dev")

    planner = LLMPlanner(
        client=FakeClient(
            _response_with_tool_call(
                "finance_profile_update",
                '{"set": {"city": "Choëx"}}',
            )
        )
    )

    plan = planner.plan("Peux-tu mettre CHOËX comme ville stp ?")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_profile_update"
    assert plan.payload == {"set": {"city": "Choëx"}}


def test_llm_planner_rewrites_incorrect_no_tool_bank_delete_fallback(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "dev")

    planner = LLMPlanner(
        client=FakeClient(
            {
                "choices": [
                    {
                        "message": {
                            "content": "Je n'ai pas d'outil pour supprimer un compte bancaire.",
                            "tool_calls": [],
                        }
                    }
                ]
            }
        )
    )

    plan = planner.plan("delete le compte test")

    assert isinstance(plan, ClarificationPlan)
    assert "finance_bank_accounts_delete" in plan.question
    assert "confirmation" in plan.question
