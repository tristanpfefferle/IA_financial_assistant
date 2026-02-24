"""Shared expenses backend services."""

from backend.services.shared_expenses.auto_share import apply_auto_share_suggestions_for_period
from backend.services.shared_expenses.effective_spending import compute_effective_spending_summary
from backend.services.shared_expenses.effective_spending_adapter import compute_effective_spending_summary_safe
from backend.services.shared_expenses.suggestion_generator import generate_initial_shared_expense_suggestions

__all__ = [
    "apply_auto_share_suggestions_for_period",
    "compute_effective_spending_summary",
    "compute_effective_spending_summary_safe",
    "generate_initial_shared_expense_suggestions",
]
