"""Unit tests for releves repository query building and category helpers."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from backend.repositories.category_utils import normalize_category_name
from backend.repositories.releves_repository import InMemoryRelevesRepository, SupabaseRelevesRepository
from shared.models import DateRange, RelevesFilters


class _ClientStub:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self._rows = rows or []

    def get_rows(self, *, table, query, with_count, use_anon_key=False):
        self.calls.append(
            {
                "table": table,
                "query": query,
                "with_count": with_count,
                "use_anon_key": use_anon_key,
            }
        )
        return self._rows, 0


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


def test_normalize_category_name_collapses_spaces_and_lowercases() -> None:
    assert normalize_category_name("  Frais   Bancaires  ") == "frais bancaires"


def test_in_memory_filter_uses_normalized_category_name() -> None:
    repository = InMemoryRelevesRepository()

    filters = RelevesFilters(
        profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        categorie="  ALIMENTATION  ",
        limit=10,
        offset=0,
    )

    rows, total = repository.list_releves(filters)

    assert total == 2
    assert len(rows) == 2


def test_get_excluded_category_names_queries_profile_categories() -> None:
    client = _ClientStub(
        rows=[
            {"name": "Frais Bancaires", "name_norm": "frais bancaires", "exclude_from_totals": True},
            {"name": "  Cashback  ", "name_norm": "", "exclude_from_totals": True},
            {"name": "Ignored", "name_norm": "ignored", "exclude_from_totals": False},
        ]
    )
    repository = SupabaseRelevesRepository(client=client)

    excluded = repository.get_excluded_category_names(UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))

    assert excluded == {"frais bancaires", "cashback"}
    assert client.calls[0]["table"] == "profile_categories"


def test_build_query_normalizes_category_filter() -> None:
    client = _ClientStub()
    repository = SupabaseRelevesRepository(client=client)

    filters = RelevesFilters(
        profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        categorie="  Alimentation  ",
        limit=10,
        offset=0,
    )

    repository.list_releves(filters)

    query = client.calls[0]["query"]
    assert ("categorie", "eq.alimentation") in query
