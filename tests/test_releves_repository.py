"""Unit tests for Supabase releves repository query building."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from backend.repositories.releves_repository import SupabaseRelevesRepository
from shared.models import DateRange, RelevesFilters


class _ClientStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def get_rows(self, *, table, query, with_count, use_anon_key=False):
        self.calls.append(
            {
                "table": table,
                "query": query,
                "with_count": with_count,
                "use_anon_key": use_anon_key,
            }
        )
        return [], 0


def test_build_query_repeats_date_key_for_date_range() -> None:
    client = _ClientStub()
    repository = SupabaseRelevesRepository(client=client)

    filters = RelevesFilters(
        profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        date_range=DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 31)),
        limit=10,
        offset=0,
    )

    repository.list_releves(filters)

    query = client.calls[0]["query"]
    assert isinstance(query, list)
    assert ("date", "gte.2025-01-01") in query
    assert ("date", "lte.2025-01-31") in query
    assert not any(key == "and" for key, _ in query)
