from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from backend.repositories.shared_expenses_repository import InMemorySharedExpensesRepository, SharedExpenseRow
from backend.services.shared_expenses.effective_spending import compute_effective_spending_summary


def test_compute_effective_spending_summary_with_incoming_and_outgoing() -> None:
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    other_profile_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

    repository = InMemorySharedExpensesRepository()
    repository._shared_expenses.extend(
        [
            SharedExpenseRow(
                from_profile_id=profile_id,
                to_profile_id=other_profile_id,
                transaction_id=None,
                amount=Decimal("100"),
                created_at=datetime(2026, 2, 10, tzinfo=timezone.utc),
                status="applied",
                split_ratio_other=Decimal("0.5"),
            ),
            SharedExpenseRow(
                from_profile_id=other_profile_id,
                to_profile_id=profile_id,
                transaction_id=None,
                amount=Decimal("60"),
                created_at=datetime(2026, 2, 12, tzinfo=timezone.utc),
                status="applied",
                split_ratio_other=Decimal("0.5"),
            ),
        ]
    )

    summary = compute_effective_spending_summary(
        profile_id=profile_id,
        start_date=date(2026, 2, 1),
        end_date=date(2026, 2, 28),
        releves_total_expense=Decimal("1000"),
        shared_expenses_repository=repository,
    )

    assert summary["outgoing"] == Decimal("100")
    assert summary["incoming"] == Decimal("60")
    assert summary["net_balance"] == Decimal("-40")
    assert summary["effective_total"] == Decimal("960")
