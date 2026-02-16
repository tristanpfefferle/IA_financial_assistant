"""Backend tool service placeholders.

All business logic must stay in backend and should be delegated to the existing
`gestion_financiere` repository via wrappers.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.repositories.releves_repository import RelevesRepository
from backend.repositories.transactions_repository import TransactionsRepository
from shared.models import (
    RelevesAggregateGroup,
    RelevesAggregateRequest,
    RelevesAggregateResult,
    RelevesFilters,
    RelevesSearchResult,
    RelevesSumResult,
    ToolError,
    ToolErrorCode,
    TransactionFilters,
    TransactionSearchResult,
    TransactionSumResult,
)


@dataclass(slots=True)
class BackendToolService:
    transactions_repository: TransactionsRepository
    releves_repository: RelevesRepository

    def search_transactions(self, filters: TransactionFilters) -> TransactionSearchResult | ToolError:
        """Deprecated alias for releves_search kept for compatibility."""

        return self.releves_search(filters)

    def sum_transactions(self, filters: TransactionFilters) -> TransactionSumResult | ToolError:
        """Deprecated alias for releves_sum kept for compatibility."""

        return self.releves_sum(filters)

    def releves_search(self, filters: RelevesFilters) -> RelevesSearchResult | ToolError:
        try:
            items, total = self.releves_repository.list_releves(filters)
            return RelevesSearchResult(items=items, limit=filters.limit, offset=filters.offset, total=total)
        except Exception as exc:  # placeholder normalization at contract boundary
            return ToolError(code=ToolErrorCode.BACKEND_ERROR, message=str(exc))

    def releves_sum(self, filters: RelevesFilters) -> RelevesSumResult | ToolError:
        try:
            total, count, currency = self.releves_repository.sum_releves(filters)
            average = (total / count) if count > 0 else total
            return RelevesSumResult(
                total=total,
                count=count,
                average=average,
                currency=currency,
                filters=filters,
            )
        except Exception as exc:  # placeholder normalization at contract boundary
            return ToolError(code=ToolErrorCode.BACKEND_ERROR, message=str(exc))

    def releves_aggregate(
        self, request: RelevesAggregateRequest
    ) -> RelevesAggregateResult | ToolError:
        try:
            aggregated, currency = self.releves_repository.aggregate_releves(request)
            groups = {
                group_key: RelevesAggregateGroup(total=total, count=count)
                for group_key, (total, count) in aggregated.items()
            }
            return RelevesAggregateResult(
                group_by=request.group_by,
                groups=groups,
                currency=currency,
                filters=request,
            )
        except Exception as exc:  # placeholder normalization at contract boundary
            return ToolError(code=ToolErrorCode.BACKEND_ERROR, message=str(exc))
