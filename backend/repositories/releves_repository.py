"""Repository interfaces and adapters for releves_bancaires transactions."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol
from uuid import UUID, uuid4

from backend.db.supabase_client import SupabaseClient
from shared.text_utils import normalize_category_name
from shared.models import (
    ReleveBancaire,
    RelevesAggregateRequest,
    RelevesDirection,
    RelevesFilters,
    RelevesGroupBy,
)


class RelevesRepository(Protocol):
    def list_releves(self, filters: RelevesFilters) -> tuple[list[ReleveBancaire], int | None]:
        """Return paginated releves plus optional total count."""

    def sum_releves(self, filters: RelevesFilters) -> tuple[Decimal, int, str | None]:
        """Return total, count and currency for releves matching filters."""

    def aggregate_releves(
        self, request: RelevesAggregateRequest
    ) -> tuple[dict[str, tuple[Decimal, int]], str | None]:
        """Return grouped totals/counts plus optional currency."""

    def get_excluded_category_names(self, profile_id: UUID) -> set[str]:
        """Return normalized category names excluded from totals for the profile."""

    def update_bank_account_id_by_ids(
        self,
        *,
        profile_id: UUID,
        releve_ids: list[UUID],
        bank_account_id: UUID,
    ) -> int:
        """Attach selected releves to the given bank account and return updated row count."""

    def update_bank_account_id_by_filters(
        self,
        *,
        profile_id: UUID,
        filters: RelevesFilters,
        bank_account_id: UUID,
    ) -> int:
        """Attach filtered releves to the given bank account and return updated row count."""

    def list_releves_for_import(
        self,
        *,
        profile_id: UUID,
        bank_account_id: UUID | None,
    ) -> list[dict[str, object]]:
        """Return rows used for dedup/compare during releves import."""

    def insert_releves_bulk(self, *, profile_id: UUID, rows: list[dict[str, object]]) -> int:
        """Insert multiple releves rows and return inserted count."""

    def delete_releves_by_ids(self, *, profile_id: UUID, releve_ids: list[UUID]) -> int:
        """Delete releves by ids and return deleted count."""


class InMemoryRelevesRepository:
    """In-memory repository used for local dev/tests when Supabase is not configured."""

    def __init__(self) -> None:
        self._profile_categories_seed: list[dict[str, object]] = [
            {
                "profile_id": UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                "name": "Transfert interne",
                "name_norm": "transfert interne",
                "exclude_from_totals": True,
            },
            {
                "profile_id": UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                "name": "Logement",
                "name_norm": "logement",
                "exclude_from_totals": False,
            },
        ]
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
            ReleveBancaire(
                id=UUID("44444444-4444-4444-4444-444444444444"),
                profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                date=date.fromisoformat("2025-01-12"),
                libelle="Virement épargne",
                montant=Decimal("-150.00"),
                devise="EUR",
                categorie="Transfert interne",
                payee="Mon compte épargne",
                merchant_id=None,
            ),
            ReleveBancaire(
                id=UUID("55555555-5555-5555-5555-555555555555"),
                profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                date=date.fromisoformat("2025-01-13"),
                libelle="Loyer janvier",
                montant=Decimal("-900.00"),
                devise="EUR",
                categorie="Logement",
                payee="Agence immobilière",
                merchant_id=None,
            ),
        ]

    def _apply_filters(self, filters: RelevesFilters | RelevesAggregateRequest) -> list[ReleveBancaire]:
        items = [item for item in self._seed if item.profile_id == filters.profile_id]

        if filters.date_range:
            start = filters.date_range.start_date
            end = filters.date_range.end_date
            items = [item for item in items if start <= item.date <= end]

        if filters.categorie:
            normalized_filter = normalize_category_name(filters.categorie)
            items = [
                item
                for item in items
                if item.categorie and normalize_category_name(item.categorie) == normalized_filter
            ]

        if filters.merchant_id:
            items = [item for item in items if item.merchant_id == filters.merchant_id]
        elif filters.merchant:
            merchant = filters.merchant.lower()
            items = [item for item in items if item.payee and merchant in item.payee.lower()]

        if filters.bank_account_id is not None:
            items = [item for item in items if item.bank_account_id == filters.bank_account_id]

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
        if filters.direction == RelevesDirection.DEBIT_ONLY:
            excluded_categories = self.get_excluded_category_names(filters.profile_id)
            if excluded_categories:
                filtered = [
                    item
                    for item in filtered
                    if not item.categorie
                    or normalize_category_name(item.categorie) not in excluded_categories
                ]
        total = sum((item.montant for item in filtered), Decimal("0"))
        currency = filtered[0].devise if filtered else None
        return total, len(filtered), currency

    def aggregate_releves(
        self, request: RelevesAggregateRequest
    ) -> tuple[dict[str, tuple[Decimal, int]], str | None]:
        filtered = self._apply_filters(request)
        if request.direction == RelevesDirection.DEBIT_ONLY:
            excluded_categories = self.get_excluded_category_names(request.profile_id)
            if excluded_categories:
                filtered = [
                    item
                    for item in filtered
                    if not item.categorie
                    or normalize_category_name(item.categorie) not in excluded_categories
                ]
        groups: dict[str, tuple[Decimal, int]] = {}

        for item in filtered:
            if request.group_by == RelevesGroupBy.CATEGORIE:
                key = item.categorie or "Autre"
            elif request.group_by == RelevesGroupBy.PAYEE:
                key = item.payee or "Inconnu"
            else:
                key = item.date.isoformat()[:7]

            current_total, current_count = groups.get(key, (Decimal("0"), 0))
            groups[key] = (current_total + item.montant, current_count + 1)

        currency = filtered[0].devise if filtered else None
        return groups, currency

    def get_excluded_category_names(self, profile_id: UUID) -> set[str]:
        excluded: set[str] = set()
        for row in self._profile_categories_seed:
            if row.get("profile_id") != profile_id or not row.get("exclude_from_totals"):
                continue

            name_norm = str(row.get("name_norm") or "").strip()
            if name_norm:
                excluded.add(normalize_category_name(name_norm))
                continue

            name = row.get("name")
            if isinstance(name, str) and name.strip():
                excluded.add(normalize_category_name(name))

        return excluded

    def update_bank_account_id_by_ids(
        self,
        *,
        profile_id: UUID,
        releve_ids: list[UUID],
        bank_account_id: UUID,
    ) -> int:
        releve_ids_set = set(releve_ids)
        updated = 0
        for index, item in enumerate(self._seed):
            if item.profile_id != profile_id or item.id not in releve_ids_set:
                continue
            self._seed[index] = item.model_copy(update={"bank_account_id": bank_account_id})
            updated += 1
        return updated

    def update_bank_account_id_by_filters(
        self,
        *,
        profile_id: UUID,
        filters: RelevesFilters,
        bank_account_id: UUID,
    ) -> int:
        scoped_filters = filters.model_copy(update={"profile_id": profile_id})
        matching_ids = [item.id for item in self._apply_filters(scoped_filters)]
        return self.update_bank_account_id_by_ids(
            profile_id=profile_id,
            releve_ids=matching_ids,
            bank_account_id=bank_account_id,
        )

    def list_releves_for_import(
        self,
        *,
        profile_id: UUID,
        bank_account_id: UUID | None,
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for item in self._seed:
            if item.profile_id != profile_id:
                continue
            if bank_account_id is not None and item.bank_account_id != bank_account_id:
                continue
            rows.append(
                {
                    "id": item.id,
                    "date": item.date,
                    "montant": item.montant,
                    "devise": item.devise,
                    "libelle": item.libelle,
                    "payee": item.payee,
                    "categorie": item.categorie,
                    "bank_account_id": item.bank_account_id,
                    "meta": None,
                    "source": None,
                }
            )
        return rows

    def insert_releves_bulk(self, *, profile_id: UUID, rows: list[dict[str, object]]) -> int:
        if not rows:
            return 0
        for row in rows:
            next_id = uuid4()
            self._seed.append(
                ReleveBancaire(
                    id=next_id,
                    profile_id=profile_id,
                    date=row["date"],
                    libelle=row.get("libelle"),
                    montant=row["montant"],
                    devise=str(row.get("devise") or "CHF"),
                    categorie=row.get("categorie"),
                    payee=row.get("payee"),
                    merchant_id=None,
                    bank_account_id=row.get("bank_account_id"),
                )
            )
        return len(rows)

    def delete_releves_by_ids(self, *, profile_id: UUID, releve_ids: list[UUID]) -> int:
        ids = set(releve_ids)
        before = len(self._seed)
        self._seed = [row for row in self._seed if not (row.profile_id == profile_id and row.id in ids)]
        return before - len(self._seed)


class SupabaseRelevesRepository:
    """Supabase-backed repository for releves_bancaires."""

    def __init__(self, client: SupabaseClient) -> None:
        self._client = client

    def _build_query(self, filters: RelevesFilters | RelevesAggregateRequest) -> list[tuple[str, str | int]]:
        query: list[tuple[str, str | int]] = [
            ("profile_id", f"eq.{filters.profile_id}"),
        ]

        if filters.date_range:
            query.append(("date", f"gte.{filters.date_range.start_date}"))
            query.append(("date", f"lte.{filters.date_range.end_date}"))

        if filters.categorie:
            query.append(("categorie", f"eq.{normalize_category_name(filters.categorie)}"))

        if filters.merchant_id:
            query.append(("merchant_id", f"eq.{filters.merchant_id}"))
        elif filters.merchant:
            query.append(("payee", f"ilike.*{filters.merchant}*"))

        if filters.bank_account_id is not None:
            query.append(("bank_account_id", f"eq.{filters.bank_account_id}"))

        if filters.direction == RelevesDirection.DEBIT_ONLY:
            query.append(("montant", "lt.0"))
        elif filters.direction == RelevesDirection.CREDIT_ONLY:
            query.append(("montant", "gt.0"))

        return query

    def list_releves(self, filters: RelevesFilters) -> tuple[list[ReleveBancaire], int | None]:
        query = [
            *self._build_query(filters),
            ("select", "id,profile_id,date,libelle,montant,devise,categorie,payee,merchant_id,bank_account_id"),
            ("limit", filters.limit),
            ("offset", filters.offset),
        ]
        rows, total = self._client.get_rows(table="releves_bancaires", query=query, with_count=True)
        return [ReleveBancaire.model_validate(row) for row in rows], total

    def sum_releves(self, filters: RelevesFilters) -> tuple[Decimal, int, str | None]:
        query = [*self._build_query(filters), ("select", "montant,devise,categorie,bank_account_id")]
        rows, _ = self._client.get_rows(table="releves_bancaires", query=query, with_count=False)

        if filters.direction == RelevesDirection.DEBIT_ONLY:
            excluded_categories = self.get_excluded_category_names(filters.profile_id)
            if excluded_categories:
                rows = [
                    row
                    for row in rows
                    if not row.get("categorie")
                    or normalize_category_name(str(row["categorie"])) not in excluded_categories
                ]

        total = Decimal("0")
        currency: str | None = None
        for row in rows:
            montant = Decimal(str(row["montant"]))
            total += montant
            if currency is None:
                currency = row.get("devise")

        return total, len(rows), currency

    def aggregate_releves(
        self, request: RelevesAggregateRequest
    ) -> tuple[dict[str, tuple[Decimal, int]], str | None]:
        query = [*self._build_query(request), ("select", "montant,devise,date,categorie,payee,bank_account_id")]
        rows, _ = self._client.get_rows(table="releves_bancaires", query=query, with_count=False)

        if request.direction == RelevesDirection.DEBIT_ONLY:
            excluded_categories = self.get_excluded_category_names(request.profile_id)
            if excluded_categories:
                rows = [
                    row
                    for row in rows
                    if not row.get("categorie")
                    or normalize_category_name(str(row["categorie"])) not in excluded_categories
                ]

        groups: dict[str, tuple[Decimal, int]] = {}
        currency: str | None = rows[0].get("devise") if rows else None

        for row in rows:
            if request.group_by == RelevesGroupBy.CATEGORIE:
                key = row.get("categorie") or "Autre"
            elif request.group_by == RelevesGroupBy.PAYEE:
                key = row.get("payee") or "Inconnu"
            else:
                key = str(row["date"])[:7]

            montant = Decimal(str(row["montant"]))
            current_total, current_count = groups.get(key, (Decimal("0"), 0))
            groups[key] = (current_total + montant, current_count + 1)

        return groups, currency

    def get_excluded_category_names(self, profile_id: UUID) -> set[str]:
        rows, _ = self._client.get_rows(
            table="profile_categories",
            query=[
                ("profile_id", f"eq.{profile_id}"),
                ("exclude_from_totals", "eq.true"),
                ("select", "name,name_norm,exclude_from_totals"),
            ],
            with_count=False,
        )

        excluded: set[str] = set()
        for row in rows:
            if not row.get("exclude_from_totals"):
                continue

            name_norm = str(row.get("name_norm") or "").strip()
            if name_norm:
                excluded.add(normalize_category_name(name_norm))
                continue

            name = row.get("name")
            if isinstance(name, str) and name.strip():
                excluded.add(normalize_category_name(name))

        return excluded

    def update_bank_account_id_by_ids(
        self,
        *,
        profile_id: UUID,
        releve_ids: list[UUID],
        bank_account_id: UUID,
    ) -> int:
        if not releve_ids:
            return 0

        ids_filter = ",".join(str(releve_id) for releve_id in releve_ids)
        rows = self._client.patch_rows(
            table="releves_bancaires",
            query={
                "profile_id": f"eq.{profile_id}",
                "id": f"in.({ids_filter})",
                "select": "id",
            },
            payload={"bank_account_id": str(bank_account_id)},
            use_anon_key=False,
        )
        return len(rows)

    def update_bank_account_id_by_filters(
        self,
        *,
        profile_id: UUID,
        filters: RelevesFilters,
        bank_account_id: UUID,
    ) -> int:
        scoped_filters = filters.model_copy(update={"profile_id": profile_id, "limit": 500, "offset": 0})
        query = [*self._build_query(scoped_filters), ("select", "id")]
        matching_rows, _ = self._client.get_rows(
            table="releves_bancaires",
            query=query,
            with_count=False,
            use_anon_key=False,
        )
        releve_ids = [UUID(str(row["id"])) for row in matching_rows if row.get("id")]
        return self.update_bank_account_id_by_ids(
            profile_id=profile_id,
            releve_ids=releve_ids,
            bank_account_id=bank_account_id,
        )

    def list_releves_for_import(
        self,
        *,
        profile_id: UUID,
        bank_account_id: UUID | None,
    ) -> list[dict[str, object]]:
        query: list[tuple[str, str | int]] = [
            ("profile_id", f"eq.{profile_id}"),
            (
                "select",
                "id,date,montant,devise,libelle,payee,categorie,bank_account_id,metadonnees,source",
            ),
            ("limit", 5000),
            ("offset", 0),
        ]
        if bank_account_id is not None:
            query.insert(1, ("bank_account_id", f"eq.{bank_account_id}"))
        rows, _ = self._client.get_rows(table="releves_bancaires", query=query, with_count=False)
        return [
            {
                "id": UUID(str(row["id"])),
                "date": date.fromisoformat(str(row["date"])),
                "montant": Decimal(str(row["montant"])),
                "devise": row.get("devise"),
                "libelle": row.get("libelle"),
                "payee": row.get("payee"),
                "categorie": row.get("categorie"),
                "bank_account_id": UUID(str(row["bank_account_id"])) if row.get("bank_account_id") else None,
                "meta": row.get("metadonnees"),
                "source": row.get("source"),
            }
            for row in rows
        ]

    def insert_releves_bulk(self, *, profile_id: UUID, rows: list[dict[str, object]]) -> int:
        if not rows:
            return 0
        payload: list[dict[str, object]] = []
        for row in rows:
            base_payload: dict[str, object] = {
                "profile_id": str(profile_id),
                "bank_account_id": str(row["bank_account_id"]) if row.get("bank_account_id") else None,
                "date": row["date"].isoformat(),
                "montant": str(row["montant"]),
                "devise": row.get("devise") or "CHF",
                "libelle": row.get("libelle"),
                "payee": row.get("payee"),
                "categorie": row.get("categorie"),
            }
            if row.get("meta") is not None:
                base_payload["metadonnees"] = row.get("meta")
            if row.get("source") is not None:
                base_payload["source"] = row.get("source")
            payload.append(base_payload)
        inserted = self._client.post_rows(table="releves_bancaires", payload=payload, use_anon_key=False)
        return len(inserted)

    def delete_releves_by_ids(self, *, profile_id: UUID, releve_ids: list[UUID]) -> int:
        if not releve_ids:
            return 0
        ids_filter = ",".join(str(releve_id) for releve_id in releve_ids)
        rows = self._client.delete_rows(
            table="releves_bancaires",
            query={
                "profile_id": f"eq.{profile_id}",
                "id": f"in.({ids_filter})",
                "select": "id",
            },
            use_anon_key=False,
        )
        return len(rows)
