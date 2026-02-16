"""Backend client abstraction for agent (in-process by default)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from backend.services.tools import BackendToolService
from shared.models import (
    CategoriesListResult,
    RelevesAggregateRequest,
    RelevesAggregateResult,
    RelevesFilters,
    RelevesSearchResult,
    RelevesSumResult,
    ProfileCategory,
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
