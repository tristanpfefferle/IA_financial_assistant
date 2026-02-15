"""Repository interfaces for transactions."""

from __future__ import annotations

from typing import Protocol

from shared.models import Transaction, TransactionFilters


class TransactionsRepository(Protocol):
    def list_transactions(self, filters: TransactionFilters) -> list[Transaction]:
        """Return transactions using typed filters."""


class GestionFinanciereTransactionsRepository:
    """Future wrapper over `gestion_financiere` transaction access functions."""

    def list_transactions(self, filters: TransactionFilters) -> list[Transaction]:
        _ = filters
        return []
