"""Tests for deterministic planning behavior."""

from datetime import date

import pytest

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




def test_planner_expenses_category_and_month_maps_category(monkeypatch) -> None:
    monkeypatch.setattr("agent.planner._today", lambda: date(2026, 2, 10))

    plan = plan_from_message("Dépenses en alimentation janvier")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_releves_sum"
    assert plan.payload["direction"] == "DEBIT_ONLY"
    assert plan.payload["categorie"] == "Alimentation"
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
    assert plan.active_task["type"] == "needs_confirmation"
    assert plan.active_task["confirmation_type"] == "confirm_delete_category"
    assert plan.active_task["context"] == {"category_name": "divers"}
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


def test_planner_bank_accounts_list_pattern() -> None:
    plan = plan_from_message("Liste mes comptes bancaires")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_bank_accounts_list"
    assert plan.payload == {}


def test_planner_bank_account_create_pattern() -> None:
    plan = plan_from_message("Ajoute un compte Epargne")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_bank_accounts_create"
    assert plan.payload == {"name": "Epargne"}




def test_planner_bank_accounts_list_affiche_pattern() -> None:
    plan = plan_from_message("Affiche mes comptes bancaires")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_bank_accounts_list"
    assert plan.payload == {}


def test_planner_bank_accounts_list_montre_and_count_patterns() -> None:
    for message in ("Montre moi mes comptes bancaires", "J'ai combien de comptes bancaires ?"):
        plan = plan_from_message(message)

        assert isinstance(plan, ToolCallPlan)
        assert plan.tool_name == "finance_bank_accounts_list"
        assert plan.payload == {}


def test_planner_bank_account_create_cree_pattern_with_punctuation() -> None:
    plan = plan_from_message("Crée un compte Compte vacances !")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_bank_accounts_create"
    assert plan.payload == {"name": "Compte vacances"}

def test_planner_bank_account_rename_pattern() -> None:
    plan = plan_from_message("Renomme le compte Courant en Compte principal")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_bank_accounts_update"
    assert plan.payload == {"name": "Courant", "set": {"name": "Compte principal"}}


def test_planner_bank_account_delete_requires_confirmation() -> None:
    plan = plan_from_message("Supprime le compte Courant")

    assert isinstance(plan, SetActiveTaskPlan)
    assert plan.active_task["type"] == "needs_confirmation"
    assert plan.active_task["confirmation_type"] == "confirm_delete_bank_account"
    assert plan.active_task["context"] == {"name": "Courant"}



def test_planner_bank_account_delete_accepts_english_variants() -> None:
    for message in ("delete le compte test", "remove le compte test", "delete account test"):
        plan = plan_from_message(message)

        assert isinstance(plan, SetActiveTaskPlan)
        assert plan.active_task["type"] == "needs_confirmation"
        assert plan.active_task["confirmation_type"] == "confirm_delete_bank_account"
        assert plan.active_task["context"] == {"name": "test"}


def test_planner_bank_account_set_default_pattern() -> None:
    plan = plan_from_message("Définis Epargne comme compte par défaut")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_bank_accounts_set_default"
    assert plan.payload == {"name": "Epargne"}


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


def test_planner_profile_get_full_profile_affiche() -> None:
    plan = plan_from_message("Affiche mon profil")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_profile_get"
    assert plan.payload == {
        "fields": [
            "first_name",
            "last_name",
            "birth_date",
            "gender",
            "address_line1",
            "address_line2",
            "postal_code",
            "city",
            "canton",
            "country",
            "personal_situation",
            "professional_situation",
        ]
    }


def test_planner_profile_get_full_profile_informations_personnelles() -> None:
    plan = plan_from_message("Quelles sont mes informations personnelles")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_profile_get"


def test_planner_profile_update_city_from_jhabite() -> None:
    plan = plan_from_message("J'habite à Zurich")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_profile_update"
    assert plan.payload == {"set": {"city": "Zurich"}}


@pytest.mark.parametrize(
    ("message", "expected_city"),
    [
        ("J’habite à Genève", "Genève"),
        ("Mon lieu de résidence est Genève", "Genève"),
        ("Ma ville est Genève", "Genève"),
        ("Je vis à Genève", "Genève"),
    ],
)
def test_planner_profile_update_city_variants(message: str, expected_city: str) -> None:
    plan = plan_from_message(message)

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_profile_update"
    assert plan.payload == {"set": {"city": expected_city}}


def test_planner_profile_update_professional_situation_from_je_suis() -> None:
    plan = plan_from_message("Je suis trader indépendant")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_profile_update"
    assert plan.payload == {"set": {"professional_situation": "trader indépendant"}}


def test_planner_profile_update_last_name() -> None:
    plan = plan_from_message("Mon nom est Dupont")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_profile_update"
    assert plan.payload == {"set": {"last_name": "Dupont"}}


def test_planner_profile_update_last_name_from_family_name_pattern() -> None:
    plan = plan_from_message("Mon nom de famille est Pfefferlé")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_profile_update"
    assert plan.payload == {"set": {"last_name": "Pfefferlé"}}


def test_planner_profile_update_postal_code() -> None:
    plan = plan_from_message("Mon code postal est 8001")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_profile_update"
    assert plan.payload == {"set": {"postal_code": "8001"}}


@pytest.mark.parametrize(
    ("message", "expected_postal_code"),
    [
        ("Mon code postal est 1200", "1200"),
        ("Mon CP est 1200", "1200"),
        ("Code postal: 1200", "1200"),
    ],
)
def test_planner_profile_update_postal_code_variants(message: str, expected_postal_code: str) -> None:
    plan = plan_from_message(message)

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_profile_update"
    assert plan.payload == {"set": {"postal_code": expected_postal_code}}


def test_planner_profile_update_professional_situation_explicit_pattern() -> None:
    plan = plan_from_message("Ma situation professionnelle est trader indépendant")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_profile_update"
    assert plan.payload == {"set": {"professional_situation": "trader indépendant"}}


def test_planner_profile_birth_date_still_prioritized_over_je_suis_pattern() -> None:
    plan = plan_from_message("Je suis né le 1998-03-12")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_profile_update"
    assert plan.payload == {"set": {"birth_date": "1998-03-12"}}


def test_planner_profile_je_suis_location_does_not_route_to_professional_situation() -> None:
    plan = plan_from_message("Je suis à la Migros")

    assert isinstance(plan, NoopPlan)


def test_profile_messages_do_not_delegate_to_llm() -> None:
    class _FailingLLMPlanner:
        def plan(self, _message: str):
            raise AssertionError("LLM planner should not be called for profile updates")

    plan = plan_from_message("J’habite à Genève", llm_planner=_FailingLLMPlanner())

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_profile_update"
    assert plan.payload == {"set": {"city": "Genève"}}


def test_planner_releves_set_bank_account_pattern(monkeypatch) -> None:
    monkeypatch.setattr("agent.planner._today", lambda: date(2025, 2, 10))

    plan = plan_from_message("Rattache les transactions au compte UBS Principal")

    assert isinstance(plan, ToolCallPlan)
    assert plan.tool_name == "finance_releves_set_bank_account"
    assert plan.payload["bank_account_name"] == "UBS Principal"
    assert plan.payload["filters"]["date_range"]["start_date"] == date(2025, 1, 11)
    assert plan.payload["filters"]["date_range"]["end_date"] == date(2025, 2, 10)
