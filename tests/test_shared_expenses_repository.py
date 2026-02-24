from datetime import date
from decimal import Decimal
from uuid import UUID

from backend.repositories.shared_expenses_repository import SupabaseSharedExpensesRepository


class _FakeSupabaseClient:
    def __init__(self, rows=None) -> None:
        self.last_query = None
        self.rows = rows or []

    def get_rows(self, *, table, query, with_count, use_anon_key):
        self.last_query = query
        return self.rows, None


def test_list_shared_expenses_for_period_does_not_use_applied_status_filter() -> None:
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    fake_client = _FakeSupabaseClient()
    repository = SupabaseSharedExpensesRepository(client=fake_client)

    repository.list_shared_expenses_for_period(
        profile_id=profile_id,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
    )

    assert isinstance(fake_client.last_query, list)
    query_pairs = dict(fake_client.last_query)
    assert query_pairs["status"] == "in.(pending,settled)"
    assert "applied" not in query_pairs["status"]


def test_list_shared_expenses_for_period_normalizes_unknown_status_to_pending() -> None:
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    fake_client = _FakeSupabaseClient(
        rows=[
            {
                "from_profile_id": str(profile_id),
                "to_profile_id": None,
                "transaction_id": None,
                "amount": "10.50",
                "created_at": "2026-01-15T10:00:00+00:00",
                "status": "weird",
                "split_ratio_other": "0.5",
                "other_party_label": None,
            }
        ]
    )
    repository = SupabaseSharedExpensesRepository(client=fake_client)

    expenses = repository.list_shared_expenses_for_period(
        profile_id=profile_id,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
    )

    assert len(expenses) == 1
    assert expenses[0].status == "pending"
    assert expenses[0].amount == Decimal("10.50")
