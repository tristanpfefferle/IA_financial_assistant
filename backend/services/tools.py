"""Backend tool service placeholders.

All business logic must stay in backend and should be delegated to the existing
`gestion_financiere` repository via wrappers.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.repositories.transactions_repository import TransactionsRepository
from shared.models import ToolError, Transaction, TransactionFilters


@dataclass(slots=True)
class BackendToolService:
    transactions_repository: TransactionsRepository

    def search_transactions(self, filters: TransactionFilters) -> list[Transaction] | ToolError:
        try:
            return self.transactions_repository.list_transactions(filters)
        except Exception as exc:  # placeholder normalization at contract boundary
            return ToolError(code="BACKEND_ERROR", message=str(exc))
