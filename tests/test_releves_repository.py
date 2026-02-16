"""Unit tests for releves repository query building and category helpers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from backend.repositories.category_utils import normalize_category_name
from backend.repositories.releves_repository import InMemoryRelevesRepository, SupabaseRelevesRepository
from shared.models import (
    DateRange,
    ReleveBancaire,
    RelevesAggregateRequest,
    RelevesDirection,
    RelevesFilters,
    RelevesGroupBy,
)


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


def test_in_memory_sum_and_aggregate_exclude_categories_for_debit_only(monkeypatch) -> None:
    repository = InMemoryRelevesRepository()
    repository._seed.append(
        ReleveBancaire(
            id=UUID("44444444-4444-4444-4444-444444444444"),
            profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            date=date(2025, 1, 12),
            libelle="Ajustement",
            montant=Decimal("-5.00"),
            devise="EUR",
            categorie=None,
            payee="Banque",
            merchant_id=None,
        )
    )
    monkeypatch.setattr(repository, "get_excluded_category_names", lambda _profile_id: {"alimentation"})

    filters = RelevesFilters(
        profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        direction=RelevesDirection.DEBIT_ONLY,
        limit=10,
        offset=0,
    )
    total, count, currency = repository.sum_releves(filters)

    assert total == Decimal("-5.00")
    assert count == 1
    assert currency == "EUR"

    aggregate_request = RelevesAggregateRequest(
        profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        direction=RelevesDirection.DEBIT_ONLY,
        group_by=RelevesGroupBy.CATEGORIE,
    )
    groups, aggregate_currency = repository.aggregate_releves(aggregate_request)

    assert groups == {"Autre": (Decimal("-5.00"), 1)}
    assert aggregate_currency == "EUR"


def test_supabase_sum_and_aggregate_exclude_categories_for_debit_only(monkeypatch) -> None:
    client = _ClientStub(
        rows=[
            {"montant": -10, "devise": "EUR", "categorie": "Alimentation", "date": "2025-01-10", "payee": "A"},
            {
                "montant": -20,
                "devise": "EUR",
                "categorie": "Transport",
                "date": "2025-01-11",
                "payee": "B",
            },
            {"montant": -3, "devise": "EUR", "categorie": None, "date": "2025-01-12", "payee": "C"},
        ]
    )
    repository = SupabaseRelevesRepository(client=client)
    monkeypatch.setattr(repository, "get_excluded_category_names", lambda _profile_id: {"alimentation"})

    filters = RelevesFilters(
        profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        direction=RelevesDirection.DEBIT_ONLY,
        limit=50,
        offset=0,
    )
    total, count, currency = repository.sum_releves(filters)

    assert total == Decimal("-23")
    assert count == 2
    assert currency == "EUR"

    aggregate_request = RelevesAggregateRequest(
        profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        direction=RelevesDirection.DEBIT_ONLY,
        group_by=RelevesGroupBy.CATEGORIE,
    )
    groups, aggregate_currency = repository.aggregate_releves(aggregate_request)

    assert groups == {
        "Transport": (Decimal("-20"), 1),
        "Autre": (Decimal("-3"), 1),
    }
    assert aggregate_currency == "EUR"


def test_supabase_credit_only_does_not_apply_excluded_categories(monkeypatch) -> None:
    client = _ClientStub(rows=[{"montant": 100, "devise": "EUR", "categorie": "Salaire"}])
    repository = SupabaseRelevesRepository(client=client)

    def _raise_if_called(_profile_id: UUID) -> set[str]:
        raise AssertionError("should not call get_excluded_category_names for CREDIT_ONLY")

    monkeypatch.setattr(repository, "get_excluded_category_names", _raise_if_called)

    filters = RelevesFilters(
        profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        direction=RelevesDirection.CREDIT_ONLY,
        limit=50,
        offset=0,
    )
    total, count, currency = repository.sum_releves(filters)

    assert total == Decimal("100")
    assert count == 1
    assert currency == "EUR"
