"""Backend tool service placeholders.

All business logic must stay in backend and should be delegated to the existing
`gestion_financiere` repository via wrappers.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.repositories.transactions_repository import TransactionsRepository
from shared.models import (
    Money,
    ToolError,
    ToolErrorCode,
    TransactionFilters,
    TransactionSearchResult,
    TransactionSumResult,
)


@dataclass(slots=True)
class BackendToolService:
    transactions_repository: TransactionsRepository

    def search_transactions(self, filters: TransactionFilters) -> TransactionSearchResult | ToolError:
        try:
            items = self.transactions_repository.list_transactions(filters)
            return TransactionSearchResult(items=items, limit=filters.limit, offset=filters.offset, total=None)
        except Exception as exc:  # placeholder normalization at contract boundary
            return ToolError(code=ToolErrorCode.BACKEND_ERROR, message=str(exc))

    def sum_transactions(self, filters: TransactionFilters) -> TransactionSumResult | ToolError:
        try:
            total_amount, count, currency = self.transactions_repository.sum_transactions(filters)
            return TransactionSumResult(
                total=Money(amount=total_amount, currency=currency),
                count=count,
                limit=filters.limit,
                offset=filters.offset,
                filters=filters,
            )
        except Exception as exc:  # placeholder normalization at contract boundary
            return ToolError(code=ToolErrorCode.BACKEND_ERROR, message=str(exc))
