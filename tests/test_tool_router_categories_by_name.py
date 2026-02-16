"""End-to-end tests for category name resolution in the tool router."""

from __future__ import annotations

from uuid import UUID

from agent.backend_client import BackendClient
from agent.tool_router import ToolRouter
from backend.repositories.categories_repository import InMemoryCategoriesRepository
from backend.repositories.releves_repository import InMemoryRelevesRepository
from backend.repositories.transactions_repository import GestionFinanciereTransactionsRepository
from backend.services.tools import BackendToolService
from shared.models import ProfileCategory, ToolError, ToolErrorCode

PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _build_router() -> ToolRouter:
    service = BackendToolService(
        transactions_repository=GestionFinanciereTransactionsRepository(),
        releves_repository=InMemoryRelevesRepository(),
        categories_repository=InMemoryCategoriesRepository(),
    )
    return ToolRouter(backend_client=BackendClient(tool_service=service))


def test_delete_category_by_name_success() -> None:
    router = _build_router()
    created = router.call(
        "finance_categories_create",
        {"name": "Divers"},
        profile_id=PROFILE_ID,
    )
    assert isinstance(created, ProfileCategory)

    deleted = router.call(
        "finance_categories_delete",
        {"category_name": "divers"},
        profile_id=PROFILE_ID,
    )

    assert deleted == {"ok": True}


def test_rename_category_by_name_success() -> None:
    router = _build_router()
    created = router.call(
        "finance_categories_create",
        {"name": "divers"},
        profile_id=PROFILE_ID,
    )
    assert isinstance(created, ProfileCategory)

    updated = router.call(
        "finance_categories_update",
        {"category_name": "Divers", "name": "Divers perso"},
        profile_id=PROFILE_ID,
    )

    assert isinstance(updated, ProfileCategory)
    assert updated.name == "Divers perso"


def test_delete_category_by_name_not_found_suggests() -> None:
    router = _build_router()
    created = router.call(
        "finance_categories_create",
        {"name": "Transfert interne"},
        profile_id=PROFILE_ID,
    )
    assert isinstance(created, ProfileCategory)

    result = router.call(
        "finance_categories_delete",
        {"category_name": "transfret interne"},
        profile_id=PROFILE_ID,
    )

    assert isinstance(result, ToolError)
    assert result.code == ToolErrorCode.NOT_FOUND
    assert result.details is not None
    assert result.details.get("close_category_names") == ["Transfert interne"]


def test_rename_category_by_name_ambiguous_errors() -> None:
    router = _build_router()
    first = router.call(
        "finance_categories_create",
        {"name": "Divers"},
        profile_id=PROFILE_ID,
    )
    second = router.call(
        "finance_categories_create",
        {"name": "DiVers"},
        profile_id=PROFILE_ID,
    )
    assert isinstance(first, ProfileCategory)
    assert isinstance(second, ProfileCategory)

    result = router.call(
        "finance_categories_update",
        {"category_name": "divers", "name": "Divers 2"},
        profile_id=PROFILE_ID,
    )

    assert isinstance(result, ToolError)
    assert result.code == ToolErrorCode.AMBIGUOUS
    assert result.details is not None
    assert result.details.get("candidates") == ["Divers", "DiVers"]
