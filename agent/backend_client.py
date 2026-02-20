"""Backend client abstraction for agent (in-process by default)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from backend.services.tools import BackendToolService
from shared.models import (
    BankAccount,
    BankAccountsListResult,
    CategoriesListResult,
    RelevesAggregateRequest,
    RelevesAggregateResult,
    RelevesFilters,
    RelevesImportRequest,
    RelevesImportResult,
    RelevesSearchResult,
    RelevesSumResult,
    ProfileCategory,
    ProfileDataResult,
    ToolError,
    TransactionFilters,
    TransactionSearchResult,
    TransactionSumResult,
)


@dataclass(slots=True)
class BackendClient:
    tool_service: BackendToolService

    def search_transactions(self, filters: TransactionFilters) -> TransactionSearchResult | ToolError:
        """Deprecated alias for releves_search kept for compatibility."""

        return self.tool_service.search_transactions(filters)

    def sum_transactions(self, filters: TransactionFilters) -> TransactionSumResult | ToolError:
        """Deprecated alias for releves_sum kept for compatibility."""

        return self.tool_service.sum_transactions(filters)

    def releves_search(self, filters: RelevesFilters) -> RelevesSearchResult | ToolError:
        return self.tool_service.releves_search(filters)

    def releves_sum(self, filters: RelevesFilters) -> RelevesSumResult | ToolError:
        return self.tool_service.releves_sum(filters)

    def releves_aggregate(
        self, request: RelevesAggregateRequest
    ) -> RelevesAggregateResult | ToolError:
        return self.tool_service.releves_aggregate(request)


    def finance_releves_set_bank_account(
        self,
        *,
        profile_id: UUID,
        bank_account_id: UUID,
        filters: dict[str, object] | None = None,
        releve_ids: list[UUID] | None = None,
    ) -> dict[str, object] | ToolError:
        return self.tool_service.finance_releves_set_bank_account(
            profile_id=profile_id,
            bank_account_id=bank_account_id,
            filters=filters,
            releve_ids=releve_ids,
        )

    def finance_releves_import_files(
        self,
        *,
        request: RelevesImportRequest,
    ) -> RelevesImportResult | ToolError:
        return self.tool_service.finance_releves_import_files(request=request)

    def finance_categories_list(self, profile_id: UUID) -> CategoriesListResult | ToolError:
        return self.tool_service.finance_categories_list(profile_id=profile_id)

    def finance_categories_create(
        self,
        *,
        profile_id: UUID,
        name: str,
        exclude_from_totals: bool = False,
    ) -> ProfileCategory | ToolError:
        return self.tool_service.finance_categories_create(
            profile_id=profile_id,
            name=name,
            exclude_from_totals=exclude_from_totals,
        )

    def finance_categories_update(
        self,
        *,
        profile_id: UUID,
        category_id: UUID,
        name: str | None = None,
        exclude_from_totals: bool | None = None,
    ) -> ProfileCategory | ToolError:
        return self.tool_service.finance_categories_update(
            profile_id=profile_id,
            category_id=category_id,
            name=name,
            exclude_from_totals=exclude_from_totals,
        )

    def finance_categories_delete(
        self, *, profile_id: UUID, category_id: UUID
    ) -> dict[str, bool] | ToolError:
        return self.tool_service.finance_categories_delete(
            profile_id=profile_id,
            category_id=category_id,
        )

    def finance_profile_get(
        self,
        *,
        profile_id: UUID,
        fields: list[str] | None = None,
    ) -> ProfileDataResult | ToolError:
        return self.tool_service.finance_profile_get(profile_id=profile_id, fields=fields)

    def finance_profile_update(
        self,
        *,
        profile_id: UUID,
        set_fields: dict[str, object | None],
    ) -> ProfileDataResult | ToolError:
        return self.tool_service.finance_profile_update(profile_id=profile_id, set_fields=set_fields)

    def finance_bank_accounts_list(self, *, profile_id: UUID) -> BankAccountsListResult | ToolError:
        return self.tool_service.finance_bank_accounts_list(profile_id=profile_id)

    def finance_bank_accounts_create(
        self,
        *,
        profile_id: UUID,
        name: str,
        kind: str | None = None,
        account_kind: str | None = None,
    ) -> BankAccount | ToolError:
        return self.tool_service.finance_bank_accounts_create(
            profile_id=profile_id,
            name=name,
            kind=kind,
            account_kind=account_kind,
        )

    def finance_bank_accounts_update(
        self,
        *,
        profile_id: UUID,
        bank_account_id: UUID,
        set_fields: dict[str, str],
    ) -> BankAccount | ToolError:
        return self.tool_service.finance_bank_accounts_update(
            profile_id=profile_id,
            bank_account_id=bank_account_id,
            set_fields=set_fields,
        )

    def finance_bank_accounts_delete(self, *, profile_id: UUID, bank_account_id: UUID) -> dict[str, bool] | ToolError:
        return self.tool_service.finance_bank_accounts_delete(
            profile_id=profile_id,
            bank_account_id=bank_account_id,
        )

    def finance_bank_accounts_can_delete(
        self,
        *,
        profile_id: UUID,
        bank_account_id: UUID,
    ) -> dict[str, object] | ToolError:
        return self.tool_service.finance_bank_accounts_can_delete(
            profile_id=profile_id,
            bank_account_id=bank_account_id,
        )

    def finance_bank_accounts_set_default(
        self,
        *,
        profile_id: UUID,
        bank_account_id: UUID,
    ) -> dict[str, object] | ToolError:
        return self.tool_service.finance_bank_accounts_set_default(
            profile_id=profile_id,
            bank_account_id=bank_account_id,
        )

    def finance_merchants_rename(
        self,
        *,
        profile_id: UUID,
        merchant_id: UUID,
        name: str,
    ) -> dict[str, str] | ToolError:
        return self.tool_service.finance_merchants_rename(
            profile_id=profile_id,
            merchant_id=merchant_id,
            name=name,
        )

    def finance_merchants_merge(
        self,
        *,
        profile_id: UUID,
        source_merchant_id: UUID,
        target_merchant_id: UUID,
    ) -> dict[str, object] | ToolError:
        return self.tool_service.finance_merchants_merge(
            profile_id=profile_id,
            source_merchant_id=source_merchant_id,
            target_merchant_id=target_merchant_id,
        )

    def finance_merchants_suggest_fixes(
        self,
        *,
        profile_id: UUID,
        status: str = "pending",
        limit: int = 50,
    ) -> dict[str, object] | ToolError:
        return self.tool_service.finance_merchants_suggest_fixes(
            profile_id=profile_id,
            status=status,
            limit=limit,
        )

    def finance_merchants_apply_suggestion(
        self,
        *,
        profile_id: UUID,
        suggestion_id: UUID,
    ) -> dict[str, object] | ToolError:
        return self.tool_service.finance_merchants_apply_suggestion(
            profile_id=profile_id,
            suggestion_id=suggestion_id,
        )
