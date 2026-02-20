"""Unit tests for finance_merchants_* backend tools."""

from __future__ import annotations

from uuid import UUID

from backend.repositories.categories_repository import InMemoryCategoriesRepository
from backend.repositories.releves_repository import InMemoryRelevesRepository
from backend.repositories.transactions_repository import GestionFinanciereTransactionsRepository
from backend.services.tools import BackendToolService
from shared.models import ToolError, ToolErrorCode

PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
MERCHANT_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
SOURCE_MERCHANT_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
TARGET_MERCHANT_ID = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")


class _RaisingProfilesRepository:
    def __init__(self, error_message: str) -> None:
        self.error_message = error_message

    def rename_merchant(self, *, profile_id: UUID, merchant_id: UUID, new_name: str) -> dict[str, str]:
        raise ValueError(self.error_message)

    def merge_merchants(
        self,
        *,
        profile_id: UUID,
        source_merchant_id: UUID,
        target_merchant_id: UUID,
    ) -> dict[str, object]:
        raise ValueError(self.error_message)


def _build_service(error_message: str) -> BackendToolService:
    return BackendToolService(
        transactions_repository=GestionFinanciereTransactionsRepository(),
        releves_repository=InMemoryRelevesRepository(),
        categories_repository=InMemoryCategoriesRepository(),
        profiles_repository=_RaisingProfilesRepository(error_message),
    )


def test_finance_merchants_rename_maps_not_found_to_not_found_error_code() -> None:
    service = _build_service("Merchant not found for this profile")

    result = service.finance_merchants_rename(
        profile_id=PROFILE_ID,
        merchant_id=MERCHANT_ID,
        name="Nouveau nom",
    )

    assert isinstance(result, ToolError)
    assert result.code == ToolErrorCode.NOT_FOUND


def test_finance_merchants_merge_maps_other_value_error_to_validation_error() -> None:
    service = _build_service("source_merchant_id and target_merchant_id must be different")

    result = service.finance_merchants_merge(
        profile_id=PROFILE_ID,
        source_merchant_id=SOURCE_MERCHANT_ID,
        target_merchant_id=TARGET_MERCHANT_ID,
    )

    assert isinstance(result, ToolError)
    assert result.code == ToolErrorCode.VALIDATION_ERROR
