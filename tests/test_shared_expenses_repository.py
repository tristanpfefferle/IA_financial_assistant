from datetime import date
from uuid import UUID

from backend.repositories.shared_expenses_repository import SupabaseSharedExpensesRepository


class _FakeSupabaseClient:
    def __init__(self) -> None:
        self.last_query = None

    def get_rows(self, *, table, query, with_count, use_anon_key):
        self.last_query = query
        return [], None


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
    assert query_pairs["status"] == "in.(active,pending)"
    assert "applied" not in query_pairs["status"]
