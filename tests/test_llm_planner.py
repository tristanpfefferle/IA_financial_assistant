"""Tests for optional LLM planner integration."""

from agent.llm_planner import LLMPlanner
from agent.planner import ErrorPlan, NoopPlan, deterministic_plan_from_message, plan_from_message


def test_llm_planner_disabled_by_default_returns_noop_plan(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("AGENT_LLM_ENABLED", raising=False)

    planner = LLMPlanner()
    plan = planner.plan("hello")

    assert isinstance(plan, NoopPlan)
    assert plan.reply == "LLM planner not enabled."


def test_llm_planner_enabled_without_api_key_returns_error(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AGENT_LLM_ENABLED", "true")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    planner = LLMPlanner()
    plan = planner.plan("hello")

    assert isinstance(plan, ErrorPlan)
    assert plan.reply == "La configuration de l'assistant IA est incomplète."
    assert plan.tool_error.code.value in {"VALIDATION_ERROR", "BACKEND_ERROR"}
    assert "OPENAI_API_KEY" in plan.tool_error.message


def test_deterministic_ping_stays_priority_over_llm_planner() -> None:
    llm_planner = LLMPlanner()

    deterministic_plan = deterministic_plan_from_message("ping")
    delegated_plan = plan_from_message("ping", llm_planner=llm_planner)

    assert isinstance(deterministic_plan, NoopPlan)
    assert deterministic_plan.reply == "pong"
    assert isinstance(delegated_plan, NoopPlan)
    assert delegated_plan.reply == "pong"


def test_llm_planner_tool_definitions_expose_search_and_sum() -> None:
    tools = LLMPlanner._tool_definition()
    names = {tool["function"]["name"] for tool in tools}

    assert names == {"finance_releves_search", "finance_releves_sum"}
