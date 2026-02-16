"""Tests for building final user replies from tool results."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from agent.answer_builder import build_final_reply
from agent.planner import ToolCallPlan
from shared.models import (
    CategoriesListResult,
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


DEBIT_ONLY_NOTE = "Certaines catégories peuvent être exclues des totaux (ex: Transfert interne)."
EXCLUDED_HELP_MESSAGE = (
    "Une catégorie exclue des totaux (ex: Transfert interne) n’est pas comptée dans les dépenses."
)


def test_build_final_reply_with_releves_sum_result() -> None:
    plan = ToolCallPlan(tool_name="finance_releves_sum", payload={}, user_reply="OK")
    result = RelevesSumResult(
        total=Decimal("-123.45"),
        count=4,
        average=Decimal("30.86"),
        currency="EUR",
        filters=RelevesFilters(profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), direction=RelevesDirection.DEBIT_ONLY),
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
        filters=RelevesFilters(profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), direction=RelevesDirection.ALL),
    )

    reply = build_final_reply(plan=plan, tool_result=result)

    assert "Total net (revenus + dépenses)" in reply
    assert "100.00 sur 2 opération(s)." in reply
    assert DEBIT_ONLY_NOTE not in reply


def test_build_final_reply_with_releves_aggregate_result_sorted_and_limited() -> None:
    plan = ToolCallPlan(tool_name="finance_releves_aggregate", payload={}, user_reply="OK")
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
    plan = ToolCallPlan(tool_name="finance_categories_list", payload={}, user_reply="OK")
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
        plan=ToolCallPlan(tool_name="finance_categories_create", payload={}, user_reply="OK"),
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

    assert reply == "Je ne trouve pas la catégorie « transfret interne ». Vouliez-vous dire: Transfert interne, Transport ?"


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
