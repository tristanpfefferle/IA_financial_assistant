"""Transaction service placeholder.

This service will wrap functions from the reference repository:
https://github.com/tristanpfefferle/gestion_financiere.git
"""

from shared.models import Transaction, TransactionFilters


class TransactionService:
    """Thin backend service wrapper over existing financial business logic."""

    def search_transactions(self, filters: TransactionFilters) -> list[Transaction]:
        """Placeholder method that will delegate to existing backend functions."""
        _ = filters
        return []
