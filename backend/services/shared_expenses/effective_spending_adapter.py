"""Safe adapter around effective spending computation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from backend.repositories.shared_expenses_repository import SharedExpensesRepository
from backend.services.shared_expenses.effective_spending import compute_effective_spending_summary


def compute_effective_spending_summary_safe(
    *,
    profile_id: UUID,
    start_date: date,
    end_date: date,
    releves_total_expense: Decimal,
    shared_expenses_repository: SharedExpensesRepository | None,
) -> dict[str, Decimal]:
    """Return effective spending with graceful fallback when shared-expenses storage is unavailable."""

    neutral_summary = {
        "outgoing": Decimal("0"),
        "incoming": Decimal("0"),
        "net_balance": Decimal("0"),
        "effective_total": abs(releves_total_expense),
    }
    if shared_expenses_repository is None:
        return neutral_summary

    try:
        return compute_effective_spending_summary(
            profile_id=profile_id,
            start_date=start_date,
            end_date=end_date,
            releves_total_expense=releves_total_expense,
            shared_expenses_repository=shared_expenses_repository,
        )
    except RuntimeError as exc:
        error_message = str(exc).lower()
        if "shared_expenses" in error_message and ("does not exist" in error_message or "not found" in error_message):
            return neutral_summary
        raise

