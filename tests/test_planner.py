"""Tests for deterministic planning behavior."""

from datetime import date

from agent.planner import ClarificationPlan, ErrorPlan, NoopPlan, SetActiveTaskPlan, ToolCallPlan, plan_from_message


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


def test_planner_expenses_chez_merchant_uses_sum_with_merchant_filter() -> None:
    plan = plan_from_message("Dépenses chez coop")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_releves_sum"
    assert plan.payload["direction"] == "DEBIT_ONLY"
    assert plan.payload["merchant"] == "coop"


def test_extract_merchant_does_not_strip_non_temporal_en() -> None:
    plan = plan_from_message("Dépenses chez coop en ligne")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_releves_sum"
    assert plan.payload["merchant"] == "coop en ligne"


def test_extract_merchant_strips_temporal_en_month() -> None:
    plan = plan_from_message("Dépenses chez migros en janvier 2026")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_releves_sum"
    assert plan.payload["merchant"] == "migros"


def test_extract_merchant_strips_temporal_multi_month() -> None:
    plan = plan_from_message("Dépenses chez migros en décembre 2025 et janvier 2026")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_releves_sum"
    assert plan.payload["merchant"] == "migros"


def test_planner_expenses_chez_merchant_and_month_are_composed(monkeypatch) -> None:
    monkeypatch.setattr("agent.planner._today", lambda: date(2025, 2, 10))

    plan = plan_from_message("Dépenses chez coop en janvier 2026")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_releves_sum"
    assert plan.payload["direction"] == "DEBIT_ONLY"
    assert plan.payload["merchant"] == "coop"
    assert plan.payload["date_range"]["start_date"] == date(2026, 1, 1)
    assert plan.payload["date_range"]["end_date"] == date(2026, 1, 31)


def test_planner_expenses_chez_merchant_two_months_builds_single_range(monkeypatch) -> None:
    monkeypatch.setattr("agent.planner._today", lambda: date(2026, 2, 16))

    plan = plan_from_message("Dépenses chez migros en décembre 2025 et janvier 2026")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_releves_sum"
    assert plan.payload["direction"] == "DEBIT_ONLY"
    assert plan.payload["merchant"] == "migros"
    assert plan.payload["date_range"]["start_date"] == date(2025, 12, 1)
    assert plan.payload["date_range"]["end_date"] == date(2026, 1, 31)


def test_planner_expenses_chez_merchant_two_months_with_repeated_en_builds_single_range(monkeypatch) -> None:
    monkeypatch.setattr("agent.planner._today", lambda: date(2026, 2, 16))

    plan = plan_from_message("Dépenses chez migros en décembre 2025 et en janvier 2026")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_releves_sum"
    assert plan.payload["direction"] == "DEBIT_ONLY"
    assert plan.payload["merchant"] == "migros"
    assert plan.payload["date_range"]["start_date"] == date(2025, 12, 1)
    assert plan.payload["date_range"]["end_date"] == date(2026, 1, 31)


def test_planner_expenses_chez_merchant_relative_two_months(monkeypatch) -> None:
    monkeypatch.setattr("agent.planner._today", lambda: date(2026, 2, 16))

    plan = plan_from_message("Dépenses chez migros ces 2 derniers mois")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_releves_sum"
    assert plan.payload["direction"] == "DEBIT_ONLY"
    assert plan.payload["merchant"] == "migros"
    assert plan.payload["date_range"]["start_date"] == date(2025, 12, 1)
    assert plan.payload["date_range"]["end_date"] == date(2026, 2, 16)


def test_planner_expenses_chez_merchant_relative_one_month(monkeypatch) -> None:
    monkeypatch.setattr("agent.planner._today", lambda: date(2026, 1, 10))

    plan = plan_from_message("Dépenses chez migros ces 1 derniers mois")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_releves_sum"
    assert plan.payload["direction"] == "DEBIT_ONLY"
    assert plan.payload["merchant"] == "migros"
    assert plan.payload["date_range"]["start_date"] == date(2025, 12, 1)
    assert plan.payload["date_range"]["end_date"] == date(2026, 1, 10)


def test_planner_expenses_chez_merchant_three_months_builds_min_max_range(monkeypatch) -> None:
    monkeypatch.setattr("agent.planner._today", lambda: date(2026, 3, 5))

    plan = plan_from_message("Dépenses chez migros en décembre 2025, janvier 2026 et février 2026")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_releves_sum"
    assert plan.payload["direction"] == "DEBIT_ONLY"
    assert plan.payload["merchant"] == "migros"
    assert plan.payload["date_range"]["start_date"] == date(2025, 12, 1)
    assert plan.payload["date_range"]["end_date"] == date(2026, 2, 28)


def test_planner_expenses_chez_merchant_single_month_still_works(monkeypatch) -> None:
    monkeypatch.setattr("agent.planner._today", lambda: date(2026, 2, 16))

    plan = plan_from_message("Dépenses chez migros en janvier 2026")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_releves_sum"
    assert plan.payload["direction"] == "DEBIT_ONLY"
    assert plan.payload["merchant"] == "migros"
    assert plan.payload["date_range"]["start_date"] == date(2026, 1, 1)
    assert plan.payload["date_range"]["end_date"] == date(2026, 1, 31)


def test_planner_expenses_chez_merchant_in_month_without_year_uses_current_year(monkeypatch) -> None:
    monkeypatch.setattr("agent.planner._today", lambda: date(2026, 2, 10))

    plan = plan_from_message("Dépenses chez coop en janvier")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_releves_sum"
    assert plan.payload["direction"] == "DEBIT_ONLY"
    assert plan.payload["merchant"] == "coop"
    assert plan.payload["date_range"]["start_date"] == date(2026, 1, 1)
    assert plan.payload["date_range"]["end_date"] == date(2026, 1, 31)


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


def test_delete_requires_confirmation() -> None:
    plan = plan_from_message("Supprime la catégorie 'divers'")

    assert isinstance(plan, SetActiveTaskPlan)
    assert plan.active_task["type"] == "confirm_delete_category"
    assert plan.active_task["category_name"] == "divers"
    assert "Répondez OUI ou NON" in plan.reply


def test_planner_categories_rename_by_name_pattern() -> None:
    plan = plan_from_message('Renomme la catégorie "divers" en "Divers"')

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_categories_update"
    assert plan.payload == {"category_name": "divers", "name": "Divers"}


def test_planner_categories_rename_without_quotes_pattern() -> None:
    plan = plan_from_message("Change le nom de la catégorie divers en Divers")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_categories_update"
    assert plan.payload == {"category_name": "divers", "name": "Divers"}


def test_planner_categories_delete_needs_clarification_when_missing_name() -> None:
    plan = plan_from_message("supprime la catégorie")

    assert isinstance(plan, ClarificationPlan)
    assert plan.question == "Quelle catégorie voulez-vous supprimer ?"


def test_planner_categories_rename_modifie_pattern() -> None:
    plan = plan_from_message("Modifie la catégorie Autres en Divers")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_categories_update"
    assert plan.payload == {"category_name": "Autres", "name": "Divers"}


def test_planner_categories_rename_change_pattern() -> None:
    plan = plan_from_message("Change la catégorie Autres en Divers")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_categories_update"
    assert plan.payload == {"category_name": "Autres", "name": "Divers"}


def test_planner_profile_get_first_name() -> None:
    plan = plan_from_message("Quel est mon prénom ?")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_profile_get"
    assert plan.payload == {"fields": ["first_name"]}


def test_planner_profile_update_first_name() -> None:
    plan = plan_from_message("Mets mon prénom à Paul")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_profile_update"
    assert plan.payload == {"set": {"first_name": "Paul"}}


def test_planner_profile_clear_first_name() -> None:
    plan = plan_from_message("Supprime mon prénom")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_profile_update"
    assert plan.payload == {"set": {"first_name": None}}


def test_planner_profile_update_birth_date() -> None:
    plan = plan_from_message("Ma date de naissance est 2001-07-14")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_profile_update"
    assert plan.payload == {"set": {"birth_date": "2001-07-14"}}


def test_planner_profile_city_question_routes_to_profile_get() -> None:
    plan = plan_from_message("Quelle est ma ville ?")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_profile_get"
    assert plan.payload == {"fields": ["city"]}


def test_planner_profile_unknown_field_returns_validation_error_plan() -> None:
    plan = plan_from_message("Quelle est ma couleur préférée ?")

    assert isinstance(plan, ErrorPlan)
    assert plan.tool_error.code.value == "VALIDATION_ERROR"
    assert plan.tool_error.details == {"field": "couleur préférée"}
