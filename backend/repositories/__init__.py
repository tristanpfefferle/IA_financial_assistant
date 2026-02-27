"""Persistence abstractions for backend services."""

from backend.repositories.shared_expenses_repository import (
    InMemorySharedExpensesRepository,
    SharedExpenseRow,
    SharedExpenseSuggestionRow,
    SharedExpensesRepository,
    SupabaseSharedExpensesRepository,
)

from backend.repositories.share_rules_repository import (
    InMemoryShareRulesRepository,
    ShareRulesRepository,
    SupabaseShareRulesRepository,
)
from backend.repositories.transaction_clusters_repository import (
    SupabaseTransactionClustersRepository,
)

__all__ = [
    "SharedExpenseRow",
    "SharedExpenseSuggestionRow",
    "SharedExpensesRepository",
    "SupabaseSharedExpensesRepository",
    "InMemorySharedExpensesRepository",
    "ShareRulesRepository",
    "SupabaseShareRulesRepository",
    "InMemoryShareRulesRepository",
    "SupabaseTransactionClustersRepository",
]
