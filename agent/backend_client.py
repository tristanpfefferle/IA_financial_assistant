"""Backend client abstraction for agent (in-process by default)."""

from __future__ import annotations

from dataclasses import dataclass

from backend.services.tools import BackendToolService
from shared.models import (
    RelevesAggregateRequest,
    RelevesAggregateResult,
    RelevesFilters,
    RelevesSearchResult,
    RelevesSumResult,
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
