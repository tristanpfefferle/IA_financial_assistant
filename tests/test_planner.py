"""Tests for deterministic planning behavior."""

from datetime import date

from agent.planner import ClarificationPlan, ErrorPlan, NoopPlan, ToolCallPlan, plan_from_message


def test_planner_ping_returns_noop_plan() -> None:
    plan = plan_from_message("ping")

    assert isinstance(plan, NoopPlan)
    assert plan.reply == "pong"


def test_planner_search_parses_date_range_filters() -> None:
    plan = plan_from_message("search: coffee from:2025-01-01 to:2025-01-31")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_releves_search"
    assert plan.payload["merchant"] == "coffee"
    assert plan.payload["date_range"]["start_date"].isoformat() == "2025-01-01"
    assert plan.payload["date_range"]["end_date"].isoformat() == "2025-01-31"


def test_planner_search_invalid_limit_stays_tool_call() -> None:
    plan = plan_from_message("search: coffee limit:0")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_releves_search"
    assert plan.payload["limit"] == 0


def test_planner_search_missing_to_returns_error_plan() -> None:
    plan = plan_from_message("search: coffee from:2025-01-01")

    assert isinstance(plan, ErrorPlan)
    assert plan.tool_error.code.value == "VALIDATION_ERROR"


def test_planner_expenses_in_future_month_requests_clarification(monkeypatch) -> None:
    monkeypatch.setattr("agent.planner._today", lambda: date(2025, 2, 10))

    plan = plan_from_message("total de mes dépenses en décembre")

    assert isinstance(plan, ClarificationPlan)
    assert plan.question == "De quelle année parlez-vous ?"


def test_planner_expenses_in_month_with_year_uses_explicit_year(monkeypatch) -> None:
    monkeypatch.setattr("agent.planner._today", lambda: date(2026, 2, 10))

    plan = plan_from_message("total de mes dépenses en janvier 2025")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_releves_sum"
    assert plan.payload["direction"] == "DEBIT_ONLY"
    assert plan.payload["date_range"]["start_date"] == date(2025, 1, 1)
    assert plan.payload["date_range"]["end_date"] == date(2025, 1, 31)


def test_planner_expenses_in_janv_uses_month_alias(monkeypatch) -> None:
    monkeypatch.setattr("agent.planner._today", lambda: date(2025, 1, 5))

    plan = plan_from_message("dépenses en janv.")

    assert isinstance(plan, ToolCallPlan)
    assert plan.payload["date_range"]["start_date"] == date(2025, 1, 1)
    assert plan.payload["date_range"]["end_date"] == date(2025, 1, 31)


def test_planner_aggregate_par_categorie() -> None:
    plan = plan_from_message("mes dépenses par catégorie")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_releves_aggregate"
    assert plan.payload["group_by"] == "categorie"
    assert plan.payload["direction"] == "DEBIT_ONLY"


def test_planner_aggregate_par_marchand() -> None:
    plan = plan_from_message("analyse par marchand")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_releves_aggregate"
    assert plan.payload["group_by"] == "payee"


def test_planner_aggregate_par_mois() -> None:
    plan = plan_from_message("répartition par mois")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_releves_aggregate"
    assert plan.payload["group_by"] == "month"


def test_planner_categories_list_pattern() -> None:
    plan = plan_from_message("liste mes catégories")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_categories_list"
    assert plan.payload == {}


def test_planner_categories_exclude_pattern() -> None:
    plan = plan_from_message("Exclus Transfert interne des totaux")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_categories_update"
    assert plan.payload == {"category_name": "Transfert interne", "exclude_from_totals": True}


def test_planner_categories_include_pattern() -> None:
    plan = plan_from_message("réintègre Transfert interne")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_categories_update"
    assert plan.payload == {"category_name": "Transfert interne", "exclude_from_totals": False}
