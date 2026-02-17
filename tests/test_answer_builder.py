"""Tests for building final user replies from tool results."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from agent.answer_builder import build_final_reply
from agent.planner import ToolCallPlan
from shared.models import (
    BankAccount,
    BankAccountsListResult,
    CategoriesListResult,
    ProfileDataResult,
    ProfileCategory,
    RelevesAggregateGroup,
    RelevesAggregateRequest,
    RelevesAggregateResult,
    RelevesDirection,
    RelevesFilters,
    RelevesGroupBy,
    RelevesSumResult,
    ToolError,
    ToolErrorCode,
)


DEBIT_ONLY_NOTE = (
    "Certaines catégories peuvent être exclues des totaux (ex: Transfert interne)."
)
EXCLUDED_HELP_MESSAGE = "Une catégorie exclue des totaux (ex: Transfert interne) n’est pas comptée dans les dépenses."


def test_build_final_reply_with_releves_sum_result() -> None:
    plan = ToolCallPlan(tool_name="finance_releves_sum", payload={}, user_reply="OK")
    result = RelevesSumResult(
        total=Decimal("-123.45"),
        count=4,
        average=Decimal("30.86"),
        currency="EUR",
        filters=RelevesFilters(
            profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            direction=RelevesDirection.DEBIT_ONLY,
        ),
    )

    reply = build_final_reply(plan=plan, tool_result=result)

    assert "123.45" in reply
    assert "-123.45" not in reply
    assert "EUR" in reply
    assert "4" in reply
    assert DEBIT_ONLY_NOTE in reply


def test_build_final_reply_with_tool_error() -> None:
    plan = ToolCallPlan(tool_name="finance_releves_sum", payload={}, user_reply="OK")
    error = ToolError(code=ToolErrorCode.BACKEND_ERROR, message="Service indisponible")

    reply = build_final_reply(plan=plan, tool_result=error)

    assert "Erreur" in reply
    assert "Service indisponible" in reply


def test_build_final_reply_with_releves_sum_all_is_neutral() -> None:
    plan = ToolCallPlan(tool_name="finance_releves_sum", payload={}, user_reply="OK")
    result = RelevesSumResult(
        total=Decimal("100.00"),
        count=2,
        average=Decimal("50.00"),
        currency=None,
        filters=RelevesFilters(
            profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            direction=RelevesDirection.ALL,
        ),
    )

    reply = build_final_reply(plan=plan, tool_result=result)

    assert "Total net (revenus + dépenses)" in reply
    assert "100.00 sur 2 opération(s)." in reply
    assert DEBIT_ONLY_NOTE not in reply


def test_build_final_reply_with_releves_aggregate_result_sorted_and_limited() -> None:
    plan = ToolCallPlan(
        tool_name="finance_releves_aggregate", payload={}, user_reply="OK"
    )
    groups = {
        f"g{i}": RelevesAggregateGroup(total=Decimal(str(-i * 10)), count=i)
        for i in range(1, 13)
    }
    result = RelevesAggregateResult(
        group_by=RelevesGroupBy.CATEGORIE,
        groups=groups,
        currency="CHF",
        filters=RelevesAggregateRequest(
            profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            group_by=RelevesGroupBy.CATEGORIE,
            direction=RelevesDirection.DEBIT_ONLY,
        ),
    )

    reply = build_final_reply(plan=plan, tool_result=result)

    assert "par categorie" in reply or "par catégorie" in reply
    assert "g12" in reply
    assert "- g1:" not in reply
    assert "Autres" in reply
    assert "-120.00" not in reply
    assert DEBIT_ONLY_NOTE in reply


def test_build_final_reply_with_categories_list_marks_excluded() -> None:
    now = datetime.now(timezone.utc)
    plan = ToolCallPlan(
        tool_name="finance_categories_list", payload={}, user_reply="OK"
    )
    result = CategoriesListResult(
        items=[
            ProfileCategory(
                id=UUID("44444444-4444-4444-4444-444444444444"),
                profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                name="Transfert interne",
                name_norm="transfert interne",
                exclude_from_totals=True,
                created_at=now,
                updated_at=now,
            )
        ]
    )

    reply = build_final_reply(plan=plan, tool_result=result)

    assert "Voici vos catégories" in reply
    assert "Transfert interne (exclue des totaux)" in reply
    assert EXCLUDED_HELP_MESSAGE in reply


def test_build_final_reply_with_categories_mutations() -> None:
    now = datetime.now(timezone.utc)
    category = ProfileCategory(
        id=UUID("44444444-4444-4444-4444-444444444444"),
        profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        name="Transport",
        name_norm="transport",
        exclude_from_totals=False,
        created_at=now,
        updated_at=now,
    )

    create_reply = build_final_reply(
        plan=ToolCallPlan(
            tool_name="finance_categories_create", payload={}, user_reply="OK"
        ),
        tool_result=category,
    )
    update_reply = build_final_reply(
        plan=ToolCallPlan(
            tool_name="finance_categories_update",
            payload={"category_name": "Transport", "name": "Mobilité"},
            user_reply="OK",
        ),
        tool_result=category,
    )
    delete_reply = build_final_reply(
        plan=ToolCallPlan(
            tool_name="finance_categories_delete",
            payload={"category_name": "Transport"},
            user_reply="OK",
        ),
        tool_result={"ok": True},
    )

    assert create_reply == "Catégorie créée: Transport."
    assert update_reply == "Catégorie renommée : Transport → Mobilité."
    assert delete_reply == "Catégorie supprimée : Transport."


def test_build_final_reply_with_bank_accounts_mutations() -> None:
    account = BankAccount(
        id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        profile_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        name="Compte courant",
        kind="individual",
        account_kind="personal_current",
        is_system=False,
    )

    create_reply = build_final_reply(
        plan=ToolCallPlan(
            tool_name="finance_bank_accounts_create",
            payload={"name": "Compte courant"},
            user_reply="OK",
        ),
        tool_result=account,
    )
    update_reply = build_final_reply(
        plan=ToolCallPlan(
            tool_name="finance_bank_accounts_update",
            payload={"name": "Compte courant", "set": {"name": "Compte principal"}},
            user_reply="OK",
        ),
        tool_result=account.model_copy(update={"name": "Compte principal"}),
    )
    delete_reply = build_final_reply(
        plan=ToolCallPlan(
            tool_name="finance_bank_accounts_delete",
            payload={"name": "Compte principal"},
            user_reply="OK",
        ),
        tool_result={"ok": True},
    )
    default_reply = build_final_reply(
        plan=ToolCallPlan(
            tool_name="finance_bank_accounts_set_default",
            payload={"name": "Compte principal"},
            user_reply="OK",
        ),
        tool_result={"ok": True, "default_bank_account_id": str(account.id)},
    )

    assert create_reply == "Compte créé: Compte courant."
    assert update_reply == "Compte mis à jour: Compte principal."
    assert delete_reply == "Compte supprimé: Compte principal."
    assert default_reply == "Compte par défaut: Compte principal."


def test_build_final_reply_with_bank_accounts_list() -> None:
    result = BankAccountsListResult(
        items=[
            BankAccount(
                id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                profile_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
                name="Courant",
                kind="individual",
                account_kind="personal_current",
                is_system=False,
            )
        ]
    )
    reply = build_final_reply(
        plan=ToolCallPlan(
            tool_name="finance_bank_accounts_list", payload={}, user_reply="OK"
        ),
        tool_result=result,
    )

    assert reply == "- Courant (personal_current, individual)"


def test_build_final_reply_with_bank_accounts_list_marks_default_account() -> None:
    ubs_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    result = BankAccountsListResult(
        items=[
            BankAccount(
                id=ubs_id,
                profile_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
                name="UBS",
                kind="individual",
                account_kind="personal_current",
                is_system=False,
            ),
            BankAccount(
                id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
                profile_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
                name="Courant",
                kind="individual",
                account_kind="personal_current",
                is_system=False,
            ),
        ],
        default_bank_account_id=ubs_id,
    )

    reply = build_final_reply(
        plan=ToolCallPlan(
            tool_name="finance_bank_accounts_list", payload={}, user_reply="OK"
        ),
        tool_result=result,
    )

    assert (
        reply
        == "- UBS ⭐ (personal_current, individual)\n- Courant (personal_current, individual)"
    )


def test_build_final_reply_with_bank_accounts_list_without_default_shows_no_star() -> (
    None
):
    result = BankAccountsListResult(
        items=[
            BankAccount(
                id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                profile_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
                name="UBS",
                kind="individual",
                account_kind="personal_current",
                is_system=False,
            ),
            BankAccount(
                id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
                profile_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
                name="Courant",
                kind="individual",
                account_kind="personal_current",
                is_system=False,
            ),
        ],
        default_bank_account_id=None,
    )

    reply = build_final_reply(
        plan=ToolCallPlan(
            tool_name="finance_bank_accounts_list", payload={}, user_reply="OK"
        ),
        tool_result=result,
    )

    assert (
        reply
        == "- UBS (personal_current, individual)\n- Courant (personal_current, individual)"
    )


def test_build_final_reply_not_found_with_suggestions() -> None:
    plan = ToolCallPlan(
        tool_name="finance_categories_update",
        payload={"category_name": "transfret interne"},
        user_reply="OK",
    )
    error = ToolError(
        code=ToolErrorCode.NOT_FOUND,
        message="Category not found for provided name.",
        details={
            "category_name": "transfret interne",
            "close_category_names": ["Transfert interne", "Transport"],
        },
    )

    reply = build_final_reply(plan=plan, tool_result=error)

    assert (
        reply
        == "Je ne trouve pas la catégorie « transfret interne ». Vouliez-vous dire: Transfert interne, Transport ?"
    )


def test_build_final_reply_not_found_without_suggestions() -> None:
    plan = ToolCallPlan(
        tool_name="finance_categories_update",
        payload={"category_name": "foo"},
        user_reply="OK",
    )
    error = ToolError(
        code=ToolErrorCode.NOT_FOUND,
        message="Category not found for provided name.",
        details={"category_name": "foo", "close_category_names": []},
    )

    reply = build_final_reply(plan=plan, tool_result=error)

    assert reply == "Je ne trouve pas la catégorie « foo ». Souhaitez-vous la créer ?"


def test_build_final_reply_profile_get_single_field_with_value() -> None:
    plan = ToolCallPlan(
        tool_name="finance_profile_get",
        payload={"fields": ["first_name"]},
        user_reply="OK",
    )
    result = ProfileDataResult(
        profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        data={"first_name": "Paul"},
    )

    reply = build_final_reply(plan=plan, tool_result=result)

    assert reply == "Votre prénom est: Paul."


def test_build_final_reply_profile_get_single_field_empty() -> None:
    plan = ToolCallPlan(
        tool_name="finance_profile_get",
        payload={"fields": ["first_name"]},
        user_reply="OK",
    )
    result = ProfileDataResult(
        profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        data={"first_name": None},
    )

    reply = build_final_reply(plan=plan, tool_result=result)

    assert reply == "Je n’ai pas votre prénom (champ vide)."


def test_build_final_reply_profile_get_multiple_fields() -> None:
    plan = ToolCallPlan(
        tool_name="finance_profile_get",
        payload={"fields": ["first_name", "last_name", "birth_date"]},
        user_reply="OK",
    )
    result = ProfileDataResult(
        profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        data={"first_name": "Paul", "last_name": "Bite", "birth_date": "2001-07-14"},
    )

    reply = build_final_reply(plan=plan, tool_result=result)

    assert reply == "- Prénom: Paul\n- Nom: Bite\n- Date de naissance: 2001-07-14"


def test_build_final_reply_profile_update_lists_changes_and_erased_fields() -> None:
    plan = ToolCallPlan(
        tool_name="finance_profile_update", payload={"set": {}}, user_reply="OK"
    )
    result = ProfileDataResult(
        profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        data={"first_name": None, "city": "Lausanne"},
    )

    reply = build_final_reply(plan=plan, tool_result=result)

    assert reply == "Infos mises à jour.\nChamp effacé: prénom.\n- Ville: Lausanne"


def test_build_final_reply_profile_validation_error_is_user_friendly() -> None:
    plan = ToolCallPlan(
        tool_name="finance_profile_get",
        payload={"fields": ["couleur préférée"]},
        user_reply="OK",
    )
    error = ToolError(
        code=ToolErrorCode.VALIDATION_ERROR,
        message="Champ de profil non reconnu.",
        details={"field": "couleur préférée"},
    )

    reply = build_final_reply(plan=plan, tool_result=error)

    assert (
        reply
        == "Je n’ai pas compris quelle info du profil vous voulez (prénom, nom, ville, etc.)."
    )


def test_build_final_reply_bank_account_delete_conflict_is_user_friendly() -> None:
    plan = ToolCallPlan(
        tool_name="finance_bank_accounts_delete",
        payload={"name": "Compte principal"},
        user_reply="OK",
    )
    error = ToolError(
        code=ToolErrorCode.CONFLICT,
        message="bank account not empty",
    )

    reply = build_final_reply(plan=plan, tool_result=error)

    assert reply == (
        "Impossible de supprimer ce compte car il contient des transactions. "
        "Déplacez/supprimez d’abord les transactions ou choisissez un autre compte."
    )
