"""Unit tests for transactions repository over releves_bancaires."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from backend.repositories.transactions_repository import SupabaseTransactionsRepository
from shared.models import DateRange, RelevesDirection, TransactionFilters


class _ClientStub:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.calls: list[dict[str, object]] = []

    def get_rows(self, *, table, query, with_count, use_anon_key=False):
        self.calls.append({"table": table, "query": query, "with_count": with_count})
        return self.rows, len(self.rows)


def test_sum_transactions_applies_date_range_and_search_filters() -> None:
    client = _ClientStub(rows=[{"montant": "-20.00", "devise": "CHF"}])
    repository = SupabaseTransactionsRepository(client=client)

    filters = TransactionFilters(
        profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        date_range=DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 31)),
        merchant="coop",
        direction=RelevesDirection.DEBIT_ONLY,
    )

    total, count, currency = repository.sum_transactions(filters)

    assert total == Decimal("-20.00")
    assert count == 1
    assert currency == "CHF"
    query = client.calls[0]["query"]
    assert ("date", "gte.2025-01-01") in query
    assert ("date", "lte.2025-01-31") in query
    assert ("or", "(libelle.ilike.*coop*,payee.ilike.*coop*)") in query




def test_sum_transactions_supports_legacy_search_filter_alias() -> None:
    client = _ClientStub(rows=[{"montant": "-20.00", "devise": "CHF"}])
    repository = SupabaseTransactionsRepository(client=client)

    filters = TransactionFilters(
        profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        date_range=None,
        search="coop",
        bank_account_id=None,
        category_id=None,
        merchant_id=None,
        direction=RelevesDirection.ALL,
        limit=50,
        offset=0,
    )

    total, count, currency = repository.sum_transactions(filters)

    assert total == Decimal("-20.00")
    assert count == 1
    assert currency == "CHF"
    query = client.calls[0]["query"]
    assert ("or", "(libelle.ilike.*coop*,payee.ilike.*coop*)") in query

def test_search_transactions_filters_bank_account_and_category() -> None:
    client = _ClientStub(
        rows=[
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "profile_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "bank_account_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "date": "2025-01-10",
                "libelle": "Migros",
                "montant": "-10.00",
                "devise": "CHF",
                "merchant_entity_id": None,
                "category_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                "payee": "Migros",
                "moyen": "carte",
                "created_at": "2025-01-10T10:00:00+00:00",
            }
        ]
    )
    repository = SupabaseTransactionsRepository(client=client)

    filters = TransactionFilters(
        profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        bank_account_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        category_id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
    )

    rows = repository.search_transactions(filters)

    assert len(rows) == 1
    assert rows[0].bank_account_id == UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    assert rows[0].category_id == UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
    query = client.calls[0]["query"]
    assert ("bank_account_id", "eq.bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb") in query
    assert ("category_id", "eq.cccccccc-cccc-cccc-cccc-cccccccccccc") in query


def test_parse_row_raises_clear_error_when_required_uuid_missing() -> None:
    row = {
        "profile_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "date": "2025-01-10",
        "montant": "-10.00",
    }

    try:
        SupabaseTransactionsRepository._parse_row(row)
    except ValueError as exc:
        assert "Missing required field 'id'" in str(exc)
    else:
        raise AssertionError("Expected ValueError when id is missing")
