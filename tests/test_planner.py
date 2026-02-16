"""Tests for deterministic planning behavior."""

from datetime import date

from agent.planner import ErrorPlan, NoopPlan, ToolCallPlan, plan_from_message


def test_planner_ping_returns_noop_plan() -> None:
    plan = plan_from_message("ping")

    assert isinstance(plan, NoopPlan)
    assert plan.reply == "pong"


def test_planner_search_parses_date_range_filters() -> None:
    plan = plan_from_message("search: coffee from:2025-01-01 to:2025-01-31")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_transactions_search"
    assert plan.payload["search"] == "coffee"
    assert plan.payload["date_range"]["start_date"].isoformat() == "2025-01-01"
    assert plan.payload["date_range"]["end_date"].isoformat() == "2025-01-31"


def test_planner_search_invalid_limit_stays_tool_call() -> None:
    plan = plan_from_message("search: coffee limit:0")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_transactions_search"
    assert plan.payload["limit"] == 0


def test_planner_search_missing_to_returns_error_plan() -> None:
    plan = plan_from_message("search: coffee from:2025-01-01")

    assert isinstance(plan, ErrorPlan)
    assert plan.tool_error.code.value == "VALIDATION_ERROR"


def test_planner_expenses_in_january_routes_to_releves_sum() -> None:
    plan = plan_from_message("total de mes dépenses en janvier")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_releves_sum"
    assert plan.payload["direction"] == "DEBIT_ONLY"
    assert plan.payload["date_range"]["start_date"] == date(date.today().year, 1, 1)
    assert plan.payload["date_range"]["end_date"] == date(date.today().year, 1, 31)
