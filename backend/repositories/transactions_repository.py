"""Transactions repository adapters.

In this codebase, "transactions" are a logical view of records stored in
`public.releves_bancaires`.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from backend.db.supabase_client import SupabaseClient
from shared.models import RelevesDirection, TransactionFilters, TransactionRow


class TransactionsRepository(Protocol):
    def search_transactions(self, filters: TransactionFilters) -> list[TransactionRow]:
        """Return paginated transactions from `releves_bancaires` using typed filters."""

    def sum_transactions(self, filters: TransactionFilters) -> tuple[Decimal, int, str | None]:
        """Return sum, count and currency for transactions from `releves_bancaires`."""


class InMemoryTransactionsRepository:
    """In-memory fallback where transactions map to releves_bancaires-like rows."""

    def __init__(self) -> None:
        self._seed: list[TransactionRow] = [
            TransactionRow(
                id=UUID("11111111-1111-1111-1111-111111111111"),
                profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                bank_account_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
                date=date.fromisoformat("2025-01-10"),
                libelle="Supermarché",
                montant=Decimal("-54.20"),
                devise="CHF",
                payee="Migros",
                moyen="carte",
            ),
            TransactionRow(
                id=UUID("22222222-2222-2222-2222-222222222222"),
                profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                bank_account_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
                date=date.fromisoformat("2025-01-11"),
                libelle="Coffee Shop",
                montant=Decimal("-12.30"),
                devise="CHF",
                payee="Coffee Shop",
                moyen="twint",
            ),
            TransactionRow(
                id=UUID("33333333-3333-3333-3333-333333333333"),
                profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                bank_account_id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
                date=date.fromisoformat("2025-01-01"),
                libelle="Salaire janvier",
                montant=Decimal("2400.00"),
                devise="CHF",
                payee="Entreprise",
                moyen="virement",
            ),
        ]

    def _filter_rows(self, filters: TransactionFilters) -> list[TransactionRow]:
        rows = [row for row in self._seed if row.profile_id == filters.profile_id]
        if filters.date_range is not None:
            rows = [
                row
                for row in rows
                if filters.date_range.start_date <= row.date <= filters.date_range.end_date
            ]
        if filters.search:
            needle = filters.search.lower()
            rows = [
                row
                for row in rows
                if needle in (row.libelle or "").lower() or needle in (row.payee or "").lower()
            ]
        if filters.bank_account_id is not None:
            rows = [row for row in rows if row.bank_account_id == filters.bank_account_id]
        if filters.category_id is not None:
            rows = [row for row in rows if row.category_id == filters.category_id]
        if filters.merchant_id is not None:
            rows = [row for row in rows if row.merchant_entity_id == filters.merchant_id]

        if filters.direction == RelevesDirection.DEBIT_ONLY:
            rows = [row for row in rows if row.montant < 0]
        elif filters.direction == RelevesDirection.CREDIT_ONLY:
            rows = [row for row in rows if row.montant > 0]
        return rows

    def search_transactions(self, filters: TransactionFilters) -> list[TransactionRow]:
        rows = self._filter_rows(filters)
        return rows[filters.offset : filters.offset + filters.limit]

    def sum_transactions(self, filters: TransactionFilters) -> tuple[Decimal, int, str | None]:
        rows = self._filter_rows(filters)
        total = sum((row.montant for row in rows), Decimal("0"))
        currency = rows[0].devise if rows else None
        return total, len(rows), currency


class SupabaseTransactionsRepository:
    """Supabase repository where transactions are read from `public.releves_bancaires`."""

    def __init__(self, client: SupabaseClient) -> None:
        self._client = client

    def _build_query(self, filters: TransactionFilters) -> list[tuple[str, str | int]]:
        query: list[tuple[str, str | int]] = [("profile_id", f"eq.{filters.profile_id}")]

        if filters.date_range is not None:
            query.append(("date", f"gte.{filters.date_range.start_date}"))
            query.append(("date", f"lte.{filters.date_range.end_date}"))

        if filters.search:
            query.append(("or", f"(libelle.ilike.*{filters.search}*,payee.ilike.*{filters.search}*)"))

        if filters.bank_account_id is not None:
            query.append(("bank_account_id", f"eq.{filters.bank_account_id}"))

        if filters.category_id is not None:
            query.append(("category_id", f"eq.{filters.category_id}"))

        if filters.merchant_id is not None:
            query.append(("merchant_entity_id", f"eq.{filters.merchant_id}"))

        if filters.direction == RelevesDirection.DEBIT_ONLY:
            query.append(("montant", "lt.0"))
        elif filters.direction == RelevesDirection.CREDIT_ONLY:
            query.append(("montant", "gt.0"))

        return query

    @staticmethod
    def _parse_row(row: dict[str, object]) -> TransactionRow:
        raw_date = row.get("date")
        if isinstance(raw_date, datetime):
            parsed_date = raw_date.date()
        elif isinstance(raw_date, date):
            parsed_date = raw_date
        else:
            parsed_date = date.fromisoformat(str(raw_date))

        created_at = row.get("created_at")
        if isinstance(created_at, datetime):
            created_at_value = created_at
        elif isinstance(created_at, str):
            created_at_value = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        else:
            created_at_value = None

        return TransactionRow(
            id=row.get("id"),
            profile_id=row.get("profile_id"),
            bank_account_id=row.get("bank_account_id"),
            date=parsed_date,
            libelle=row.get("libelle"),
            montant=Decimal(str(row.get("montant"))),
            devise=str(row.get("devise") or "CHF"),
            merchant_entity_id=row.get("merchant_entity_id"),
            category_id=row.get("category_id"),
            payee=row.get("payee"),
            moyen=row.get("moyen"),
            created_at=created_at_value,
        )

    def search_transactions(self, filters: TransactionFilters) -> list[TransactionRow]:
        query = [
            *self._build_query(filters),
            (
                "select",
                "id,profile_id,bank_account_id,date,libelle,montant,devise,merchant_entity_id,category_id,payee,moyen,created_at",
            ),
            ("limit", filters.limit),
            ("offset", filters.offset),
        ]
        rows, _ = self._client.get_rows(table="releves_bancaires", query=query, with_count=False)
        return [self._parse_row(row) for row in rows]

    def sum_transactions(self, filters: TransactionFilters) -> tuple[Decimal, int, str | None]:
        query = [*self._build_query(filters), ("select", "montant,devise")]
        rows, _ = self._client.get_rows(table="releves_bancaires", query=query, with_count=False)

        total = Decimal("0")
        currency: str | None = None
        for row in rows:
            total += Decimal(str(row.get("montant")))
            if currency is None:
                raw_currency = row.get("devise")
                currency = str(raw_currency) if raw_currency is not None else None

        return total, len(rows), currency


# Backward-compatible name kept for existing imports.
GestionFinanciereTransactionsRepository = InMemoryTransactionsRepository
