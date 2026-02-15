"""Tests for optional LLM planner integration."""

from agent.llm_planner import LLMPlanner
from agent.planner import NoopPlan, deterministic_plan_from_message, plan_from_message


def test_llm_planner_stub_returns_noop_plan() -> None:
    planner = LLMPlanner()

    plan = planner.plan("hello")

    assert isinstance(plan, NoopPlan)
    assert plan.reply == "LLM planner not enabled."


def test_deterministic_ping_stays_priority_over_llm_planner() -> None:
    llm_planner = LLMPlanner()

    deterministic_plan = deterministic_plan_from_message("ping")
    delegated_plan = plan_from_message("ping", llm_planner=llm_planner)

    assert isinstance(deterministic_plan, NoopPlan)
    assert deterministic_plan.reply == "pong"
    assert isinstance(delegated_plan, NoopPlan)
    assert delegated_plan.reply == "pong"
