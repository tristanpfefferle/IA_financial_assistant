"""Transaction repository contract (no concrete DB logic yet)."""

from __future__ import annotations

from typing import Protocol

from shared.models import Transaction, TransactionFilters


class TransactionRepository(Protocol):
    """Repository abstraction for transaction operations."""

    def search(self, filters: TransactionFilters) -> list[Transaction]:
        """Return transactions matching filters."""
        ...
