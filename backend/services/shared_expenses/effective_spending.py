"""Effective spending summary using shared expenses adjustments."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from backend.repositories.shared_expenses_repository import SharedExpensesRepository


def compute_effective_spending_summary(
    *,
    profile_id: UUID,
    start_date: date,
    end_date: date,
    releves_total_expense: Decimal,
    shared_expenses_repository: SharedExpensesRepository,
) -> dict[str, Decimal]:
    """Compute outgoing/incoming share adjustments and effective spending total."""

    rows = shared_expenses_repository.list_shared_expenses_for_period(
        profile_id=profile_id,
        start_date=start_date,
        end_date=end_date,
    )

    outgoing = Decimal("0")
    incoming = Decimal("0")
    for row in rows:
        if row.from_profile_id == profile_id:
            outgoing += row.amount
        if row.to_profile_id == profile_id:
            incoming += row.amount

    net_balance = incoming - outgoing
    effective_total = abs(releves_total_expense) - outgoing + incoming
    return {
        "outgoing": outgoing,
        "incoming": incoming,
        "net_balance": net_balance,
        "effective_total": effective_total,
    }
