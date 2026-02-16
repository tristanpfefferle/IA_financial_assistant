"""Repository interfaces and adapters for releves_bancaires transactions."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from backend.db.supabase_client import SupabaseClient
from shared.models import ReleveBancaire, RelevesDirection, RelevesFilters


class RelevesRepository(Protocol):
    def list_releves(self, filters: RelevesFilters) -> tuple[list[ReleveBancaire], int | None]:
        """Return paginated releves plus optional total count."""

    def sum_releves(self, filters: RelevesFilters) -> tuple[Decimal, int, str | None]:
        """Return total, count and currency for releves matching filters."""


class InMemoryRelevesRepository:
    """In-memory repository used for local dev/tests when Supabase is not configured."""

    def __init__(self) -> None:
        self._seed: list[ReleveBancaire] = [
            ReleveBancaire(
                id=UUID("11111111-1111-1111-1111-111111111111"),
                profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                date=date.fromisoformat("2025-01-01"),
                libelle="Salaire janvier",
                montant=Decimal("2400.00"),
                devise="EUR",
                categorie="revenu",
                payee="Entreprise",
                merchant_id=None,
            ),
            ReleveBancaire(
                id=UUID("22222222-2222-2222-2222-222222222222"),
                profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                date=date.fromisoformat("2025-01-10"),
                libelle="Supermarché",
                montant=Decimal("-54.20"),
                devise="EUR",
                categorie="alimentation",
                payee="Carrefour",
                merchant_id=None,
            ),
            ReleveBancaire(
                id=UUID("33333333-3333-3333-3333-333333333333"),
                profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                date=date.fromisoformat("2025-01-11"),
                libelle="Café",
                montant=Decimal("-12.30"),
                devise="EUR",
                categorie="alimentation",
                payee="Coffee Shop",
                merchant_id=None,
            ),
        ]

    def _apply_filters(self, filters: RelevesFilters) -> list[ReleveBancaire]:
        items = [item for item in self._seed if item.profile_id == filters.profile_id]

        if filters.date_range:
            start = filters.date_range.start_date
            end = filters.date_range.end_date
            items = [item for item in items if start <= item.date <= end]

        if filters.categorie:
            items = [item for item in items if item.categorie == filters.categorie]

        if filters.merchant_id:
            items = [item for item in items if item.merchant_id == filters.merchant_id]
        elif filters.merchant:
            merchant = filters.merchant.lower()
            items = [item for item in items if item.payee and merchant in item.payee.lower()]

        if filters.direction == RelevesDirection.DEBIT_ONLY:
            items = [item for item in items if item.montant < 0]
        elif filters.direction == RelevesDirection.CREDIT_ONLY:
            items = [item for item in items if item.montant > 0]

        return items

    def list_releves(self, filters: RelevesFilters) -> tuple[list[ReleveBancaire], int | None]:
        filtered = self._apply_filters(filters)
        start = filters.offset
        end = filters.offset + filters.limit
        return filtered[start:end], len(filtered)

    def sum_releves(self, filters: RelevesFilters) -> tuple[Decimal, int, str | None]:
        filtered = self._apply_filters(filters)
        total = sum((item.montant for item in filtered), Decimal("0"))
        currency = filtered[0].devise if filtered else None
        return total, len(filtered), currency


class SupabaseRelevesRepository:
    """Supabase-backed repository for releves_bancaires."""

    def __init__(self, client: SupabaseClient) -> None:
        self._client = client

    def _build_query(self, filters: RelevesFilters) -> list[tuple[str, str | int]]:
        query: list[tuple[str, str | int]] = [
            ("profile_id", f"eq.{filters.profile_id}"),
        ]

        if filters.date_range:
            query.append(("date", f"gte.{filters.date_range.start_date}"))
            query.append(("date", f"lte.{filters.date_range.end_date}"))

        if filters.categorie:
            query.append(("categorie", f"eq.{filters.categorie}"))

        if filters.merchant_id:
            query.append(("merchant_id", f"eq.{filters.merchant_id}"))
        elif filters.merchant:
            query.append(("payee", f"ilike.*{filters.merchant}*"))

        if filters.direction == RelevesDirection.DEBIT_ONLY:
            query.append(("montant", "lt.0"))
        elif filters.direction == RelevesDirection.CREDIT_ONLY:
            query.append(("montant", "gt.0"))

        return query

    def list_releves(self, filters: RelevesFilters) -> tuple[list[ReleveBancaire], int | None]:
        query = [
            *self._build_query(filters),
            ("select", "id,profile_id,date,libelle,montant,devise,categorie,payee,merchant_id"),
            ("limit", filters.limit),
            ("offset", filters.offset),
        ]
        rows, total = self._client.get_rows(table="releves_bancaires", query=query, with_count=True)
        return [ReleveBancaire.model_validate(row) for row in rows], total

    def sum_releves(self, filters: RelevesFilters) -> tuple[Decimal, int, str | None]:
        query = [*self._build_query(filters), ("select", "montant,devise")]
        rows, _ = self._client.get_rows(table="releves_bancaires", query=query, with_count=False)

        total = Decimal("0")
        currency: str | None = None
        for row in rows:
            montant = Decimal(str(row["montant"]))
            total += montant
            if currency is None:
                currency = row.get("devise")

        return total, len(rows), currency
