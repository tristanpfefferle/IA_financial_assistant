"""Unit tests for finance_categories_* backend tools."""

from __future__ import annotations

from uuid import UUID

from backend.repositories.categories_repository import InMemoryCategoriesRepository
from backend.repositories.releves_repository import InMemoryRelevesRepository
from backend.repositories.transactions_repository import GestionFinanciereTransactionsRepository
from backend.services.tools import BackendToolService
from shared.models import CategoriesListResult, ProfileCategory, ToolError, ToolErrorCode


def _build_tool_service() -> BackendToolService:
    return BackendToolService(
        transactions_repository=GestionFinanciereTransactionsRepository(),
        releves_repository=InMemoryRelevesRepository(),
        categories_repository=InMemoryCategoriesRepository(),
    )


def test_finance_categories_list_filters_by_profile_id() -> None:
    service = _build_tool_service()
    profile_a = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    profile_b = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

    service.finance_categories_create(profile_id=profile_a, name="Logement")
    service.finance_categories_create(profile_id=profile_b, name="Autre profil")

    result = service.finance_categories_list(profile_id=profile_a)

    assert isinstance(result, CategoriesListResult)
    assert len(result.items) == 1
    assert result.items[0].profile_id == profile_a


def test_finance_categories_create_returns_profile_category() -> None:
    service = _build_tool_service()
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    result = service.finance_categories_create(
        profile_id=profile_id,
        name="  Frais   Pro  ",
        exclude_from_totals=True,
    )

    assert isinstance(result, ProfileCategory)
    assert result.profile_id == profile_id
    assert result.name_norm == "frais pro"
    assert result.exclude_from_totals is True


def test_finance_categories_update_returns_not_found_for_other_profile() -> None:
    service = _build_tool_service()
    profile_a = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    profile_b = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

    created = service.finance_categories_create(profile_id=profile_a, name="Transport")
    assert isinstance(created, ProfileCategory)

    result = service.finance_categories_update(
        profile_id=profile_b,
        category_id=created.id,
        name="Transport pro",
    )

    assert isinstance(result, ToolError)
    assert result.code == ToolErrorCode.NOT_FOUND


def test_finance_categories_delete_returns_ok() -> None:
    service = _build_tool_service()
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    created = service.finance_categories_create(profile_id=profile_id, name="Santé")
    assert isinstance(created, ProfileCategory)

    result = service.finance_categories_delete(profile_id=profile_id, category_id=created.id)

    assert result == {"ok": True}
    remaining = service.finance_categories_list(profile_id=profile_id)
    assert isinstance(remaining, CategoriesListResult)
    assert len(remaining.items) == 0
