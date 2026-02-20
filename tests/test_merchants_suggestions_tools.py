from __future__ import annotations

from uuid import UUID

from backend.repositories.categories_repository import InMemoryCategoriesRepository
from backend.repositories.releves_repository import InMemoryRelevesRepository
from backend.repositories.transactions_repository import GestionFinanciereTransactionsRepository
from backend.services.tools import BackendToolService

PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SUGGESTION_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
SOURCE_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
TARGET_ID = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")


class _ProfilesRepositoryStub:
    def __init__(self) -> None:
        self.status_updates: list[tuple[UUID, str, str | None]] = []
        self.rename_calls: list[tuple[UUID, UUID, str]] = []
        self.merge_calls: list[tuple[UUID, UUID, UUID]] = []
        self.category_calls: list[tuple[UUID, str]] = []

    def list_merchant_suggestions(self, *, profile_id: UUID, status: str = "pending", limit: int = 100):
        assert profile_id == PROFILE_ID
        assert status == "pending"
        assert limit == 10
        return [{"id": str(SUGGESTION_ID), "status": "pending"}]

    def get_merchant_suggestion_by_id(self, *, profile_id: UUID, suggestion_id: UUID):
        assert profile_id == PROFILE_ID
        assert suggestion_id == SUGGESTION_ID
        return {
            "id": str(SUGGESTION_ID),
            "action": "rename",
            "source_merchant_id": str(SOURCE_ID),
            "suggested_name": "Marchand Propre",
        }

    def rename_merchant(self, *, profile_id: UUID, merchant_id: UUID, new_name: str):
        self.rename_calls.append((profile_id, merchant_id, new_name))
        return {"merchant_id": str(merchant_id), "name": new_name, "name_norm": "marchand propre"}

    def merge_merchants(self, *, profile_id: UUID, source_merchant_id: UUID, target_merchant_id: UUID):
        self.merge_calls.append((profile_id, source_merchant_id, target_merchant_id))
        return {"target_merchant_id": str(target_merchant_id)}

    def update_merchant_category(self, *, merchant_id: UUID, category_name: str):
        self.category_calls.append((merchant_id, category_name))

    def update_merchant_suggestion_status(
        self,
        *,
        profile_id: UUID,
        suggestion_id: UUID,
        status: str,
        error: str | None = None,
    ) -> None:
        assert profile_id == PROFILE_ID
        self.status_updates.append((suggestion_id, status, error))


def _build_service(repo: _ProfilesRepositoryStub) -> BackendToolService:
    return BackendToolService(
        transactions_repository=GestionFinanciereTransactionsRepository(),
        releves_repository=InMemoryRelevesRepository(),
        categories_repository=InMemoryCategoriesRepository(),
        profiles_repository=repo,
    )


def test_finance_merchants_suggest_fixes_returns_items() -> None:
    repo = _ProfilesRepositoryStub()
    service = _build_service(repo)

    result = service.finance_merchants_suggest_fixes(profile_id=PROFILE_ID, status="pending", limit=10)

    assert result == {"items": [{"id": str(SUGGESTION_ID), "status": "pending"}], "count": 1}


def test_finance_merchants_apply_suggestion_rename_and_status_update() -> None:
    repo = _ProfilesRepositoryStub()
    service = _build_service(repo)

    result = service.finance_merchants_apply_suggestion(profile_id=PROFILE_ID, suggestion_id=SUGGESTION_ID)

    assert result["ok"] is True
    assert repo.rename_calls == [(PROFILE_ID, SOURCE_ID, "Marchand Propre")]
    assert repo.status_updates[-1] == (SUGGESTION_ID, "applied", None)


def test_finance_merchants_apply_suggestion_merge_maps_to_merge() -> None:
    class _MergeRepo(_ProfilesRepositoryStub):
        def get_merchant_suggestion_by_id(self, *, profile_id: UUID, suggestion_id: UUID):
            return {
                "id": str(suggestion_id),
                "action": "merge",
                "source_merchant_id": str(SOURCE_ID),
                "target_merchant_id": str(TARGET_ID),
            }

    repo = _MergeRepo()
    service = _build_service(repo)
    result = service.finance_merchants_apply_suggestion(profile_id=PROFILE_ID, suggestion_id=SUGGESTION_ID)

    assert result["ok"] is True
    assert repo.merge_calls == [(PROFILE_ID, SOURCE_ID, TARGET_ID)]
    assert repo.status_updates[-1] == (SUGGESTION_ID, "applied", None)


def test_finance_merchants_apply_suggestion_categorize_maps_to_update_category() -> None:
    class _CategorizeRepo(_ProfilesRepositoryStub):
        def get_merchant_suggestion_by_id(self, *, profile_id: UUID, suggestion_id: UUID):
            return {
                "id": str(suggestion_id),
                "action": "categorize",
                "source_merchant_id": str(SOURCE_ID),
                "suggested_category": "Transport",
            }

    repo = _CategorizeRepo()
    service = _build_service(repo)
    result = service.finance_merchants_apply_suggestion(profile_id=PROFILE_ID, suggestion_id=SUGGESTION_ID)

    assert result["ok"] is True
    assert repo.category_calls == [(SOURCE_ID, "Transport")]
    assert repo.status_updates[-1] == (SUGGESTION_ID, "applied", None)
