"""Repository interfaces and adapters for releves_bancaires transactions."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol
import unicodedata
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




def _category_norm_candidates(value: str) -> tuple[str, ...]:
    normalized = normalize_category_name(value)
    if not normalized:
        return ()
    candidates = {normalized}
    if normalized.endswith("s") and len(normalized) > 1:
        candidates.add(normalized[:-1])
    else:
        candidates.add(f"{normalized}s")
    return tuple(candidates)


class RelevesRepository(Protocol):
    def list_releves(self, filters: RelevesFilters) -> tuple[list[ReleveBancaire], int | None]:
        """Return paginated releves plus optional total count."""

    def sum_releves(self, filters: RelevesFilters) -> tuple[Decimal, int, str | None]:
        """Return total, count and currency for releves matching filters."""

    def aggregate_releves(
        self, request: RelevesAggregateRequest
    ) -> tuple[dict[str, tuple[Decimal, int]], str | None]:
        """Return grouped totals/counts plus optional currency."""

    def list_pending_categorization_releves(
        self,
        *,
        profile_id: UUID,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        """Return releves pending categorization, excluding internal transfers by default."""

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
        self._import_sidecar: dict[UUID, dict[str, object]] = {}

    def _apply_filters(self, filters: RelevesFilters | RelevesAggregateRequest) -> list[ReleveBancaire]:
        items = [item for item in self._seed if item.profile_id == filters.profile_id]

        if filters.date_range:
            start = filters.date_range.start_date
            end = filters.date_range.end_date
            items = [item for item in items if start <= item.date <= end]

        if filters.category_id:
            items = [item for item in items if item.category_id == filters.category_id]
        elif filters.categorie:
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

    def _is_internal_transfer(self, item: ReleveBancaire) -> bool:
        sidecar = self._import_sidecar.get(item.id, {})
        meta = sidecar.get("meta")
        if isinstance(meta, dict) and str(meta.get("tx_kind") or "").strip().lower() == "transfer_internal":
            return True
        return bool(item.categorie and normalize_category_name(item.categorie) in {"transferts internes", "transfert interne"})

    def list_releves(self, filters: RelevesFilters) -> tuple[list[ReleveBancaire], int | None]:
        filtered = self._apply_filters(filters)
        start = filters.offset
        end = filters.offset + filters.limit
        return filtered[start:end], len(filtered)

    def sum_releves(self, filters: RelevesFilters) -> tuple[Decimal, int, str | None]:
        filtered = self._apply_filters(filters)
        if not filters.include_internal_transfers:
            filtered = [item for item in filtered if not self._is_internal_transfer(item)]
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
        if not request.include_internal_transfers:
            filtered = [item for item in filtered if not self._is_internal_transfer(item)]
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

        category_names_by_id: dict[UUID, str] = {}
        category_names_by_norm: dict[str, str] = {}
        if request.group_by == RelevesGroupBy.CATEGORIE:
            for row in self._profile_categories_seed:
                if row.get("profile_id") != request.profile_id:
                    continue
                category_id = row.get("id")
                category_name = row.get("name")
                if isinstance(category_name, str) and category_name.strip():
                    cleaned_name = category_name.strip()
                    if isinstance(category_id, UUID):
                        category_names_by_id[category_id] = cleaned_name
                    norm_source_raw = row.get("name_norm") or row.get("name") or row.get("system_key")
                    norm_source = str(norm_source_raw).strip() if norm_source_raw else ""
                    if norm_source:
                        for candidate_norm in _category_norm_candidates(norm_source):
                            category_names_by_norm[candidate_norm] = cleaned_name

        for item in filtered:
            if request.group_by == RelevesGroupBy.CATEGORIE:
                key: str | None = None
                if isinstance(item.category_id, UUID):
                    key = category_names_by_id.get(item.category_id)

                if key is None and isinstance(item.categorie, str) and item.categorie.strip():
                    normalized_candidates = _category_norm_candidates(item.categorie)
                    key = next((category_names_by_norm.get(candidate) for candidate in normalized_candidates if category_names_by_norm.get(candidate)), None) or item.categorie

                if key is None:
                    key = "Autres"
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

    def list_pending_categorization_releves(
        self,
        *,
        profile_id: UUID,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for item in self._seed:
            if item.profile_id != profile_id or self._is_internal_transfer(item):
                continue

            sidecar = self._import_sidecar.get(item.id, {})
            meta = sidecar.get("meta") if isinstance(sidecar.get("meta"), dict) else {}
            category_status = str(meta.get("category_status") or "").strip().lower()
            category_key = str(meta.get("category_key") or "").strip().lower()
            if category_status != "pending" and category_key != "twint_p2p_pending":
                continue

            rows.append(
                {
                    "id": str(item.id),
                    "date": item.date.isoformat(),
                    "montant": item.montant,
                    "devise": item.devise,
                    "libelle": item.libelle,
                    "payee": item.payee,
                    "categorie": item.categorie,
                    "meta": {
                        "category_key": meta.get("category_key"),
                        "category_status": meta.get("category_status"),
                    },
                }
            )
            if len(rows) >= limit:
                break

        return rows

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
            sidecar = self._import_sidecar.get(item.id, {})
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
                    "meta": sidecar.get("meta"),
                    "source": sidecar.get("source"),
                    "contenu_brut": sidecar.get("contenu_brut"),
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
                    merchant_id=row.get("merchant_entity_id"),
                    category_id=row.get("category_id"),
                    bank_account_id=row.get("bank_account_id"),
                )
            )
            self._import_sidecar[next_id] = {
                "meta": row.get("meta"),
                "source": row.get("source"),
                "contenu_brut": row.get("contenu_brut"),
            }
        return len(rows)

    def delete_releves_by_ids(self, *, profile_id: UUID, releve_ids: list[UUID]) -> int:
        ids = set(releve_ids)
        before = len(self._seed)
        self._seed = [row for row in self._seed if not (row.profile_id == profile_id and row.id in ids)]
        for releve_id in ids:
            self._import_sidecar.pop(releve_id, None)
        return before - len(self._seed)


class SupabaseRelevesRepository:
    """Supabase-backed repository for releves_bancaires."""

    def __init__(self, client: SupabaseClient) -> None:
        self._client = client

    @staticmethod
    def _normalize_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value.strip().lower()).encode("ascii", "ignore").decode("ascii")
        return " ".join(normalized.split())

    def _resolve_merchant_ids(self, *, profile_id: UUID, merchant_query: str) -> list[UUID]:
        normalized_query = self._normalize_text(merchant_query)
        if not normalized_query:
            return []

        rows, _ = self._client.get_rows(
            table="merchants",
            query={
                "select": "id,name,name_norm,aliases",
                "profile_id": f"eq.{profile_id}",
                "scope": "eq.personal",
                "limit": 500,
            },
            with_count=False,
            use_anon_key=False,
        )

        matching_ids: list[UUID] = []
        raw_query = merchant_query.strip().lower()
        for row in rows:
            merchant_id_raw = row.get("id")
            if not merchant_id_raw:
                continue

            name_norm = self._normalize_text(str(row.get("name_norm") or ""))
            name = str(row.get("name") or "").strip().lower()
            aliases_raw = row.get("aliases")
            aliases = aliases_raw if isinstance(aliases_raw, list) else []

            matches_name_norm = name_norm == normalized_query
            matches_name = bool(raw_query and raw_query in name)
            matches_alias = any(raw_query in str(alias).strip().lower() for alias in aliases if isinstance(alias, str))
            if matches_name_norm or matches_name or matches_alias:
                matching_ids.append(UUID(str(merchant_id_raw)))

        return matching_ids

    def _build_query(self, filters: RelevesFilters | RelevesAggregateRequest) -> list[tuple[str, str | int]]:
        query: list[tuple[str, str | int]] = [
            ("profile_id", f"eq.{filters.profile_id}"),
        ]

        if filters.date_range:
            query.append(("date", f"gte.{filters.date_range.start_date}"))
            query.append(("date", f"lte.{filters.date_range.end_date}"))

        if filters.category_id:
            query.append(("category_id", f"eq.{filters.category_id}"))
        elif filters.categorie:
            query.append(("categorie", f"eq.{filters.categorie.strip()}"))

        if filters.merchant_id:
            query.append(("merchant_id", f"eq.{filters.merchant_id}"))
        elif filters.merchant:
            # Merchant text filters first resolve to merchants.id for this profile. If no
            # merchant matches, we keep backward compatibility with a payee/libelle ILIKE fallback.
            merchant_ids = self._resolve_merchant_ids(
                profile_id=filters.profile_id,
                merchant_query=filters.merchant,
            )
            if merchant_ids:
                ids = ",".join(str(merchant_id) for merchant_id in merchant_ids)
                query.append(("merchant_id", f"in.({ids})"))
            else:
                query.append(("or", f"(payee.ilike.*{filters.merchant}*,libelle.ilike.*{filters.merchant}*)"))

        if filters.bank_account_id is not None:
            query.append(("bank_account_id", f"eq.{filters.bank_account_id}"))

        if filters.direction == RelevesDirection.DEBIT_ONLY:
            query.append(("montant", "lt.0"))
        elif filters.direction == RelevesDirection.CREDIT_ONLY:
            query.append(("montant", "gt.0"))

        return query

    @staticmethod
    def _row_is_internal_transfer(row: dict[str, object]) -> bool:
        meta = row.get("metadonnees")
        if isinstance(meta, dict) and str(meta.get("tx_kind") or "").strip().lower() == "transfer_internal":
            return True
        category = row.get("categorie")
        return isinstance(category, str) and normalize_category_name(category) in {"transferts internes", "transfert interne"}

    def list_releves(self, filters: RelevesFilters) -> tuple[list[ReleveBancaire], int | None]:
        query = [
            *self._build_query(filters),
            ("select", "id,profile_id,date,libelle,montant,devise,categorie,category_id,payee,merchant_id,bank_account_id"),
            ("limit", filters.limit),
            ("offset", filters.offset),
        ]
        rows, total = self._client.get_rows(table="releves_bancaires", query=query, with_count=True)
        return [ReleveBancaire.model_validate(row) for row in rows], total

    def sum_releves(self, filters: RelevesFilters) -> tuple[Decimal, int, str | None]:
        query = [*self._build_query(filters), ("select", "montant,devise,categorie,bank_account_id,metadonnees")]
        rows, _ = self._client.get_rows(table="releves_bancaires", query=query, with_count=False)

        if not filters.include_internal_transfers:
            rows = [row for row in rows if not self._row_is_internal_transfer(row)]

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
        query = [
            *self._build_query(request),
            ("select", "montant,devise,date,categorie,category_id,payee,bank_account_id,metadonnees"),
        ]
        rows, _ = self._client.get_rows(table="releves_bancaires", query=query, with_count=False)

        if not request.include_internal_transfers:
            rows = [row for row in rows if not self._row_is_internal_transfer(row)]

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
        category_names_by_id: dict[str, str] = {}
        category_names_by_norm: dict[str, str] = {}
        if request.group_by == RelevesGroupBy.CATEGORIE:
            categories_rows, _ = self._client.get_rows(
                table="profile_categories",
                query=[
                    ("profile_id", f"eq.{request.profile_id}"),
                    ("select", "id,name,name_norm,system_key"),
                    ("limit", 500),
                ],
                with_count=False,
            )
            for category_row in categories_rows:
                raw_id = category_row.get("id")
                raw_name = category_row.get("name")
                if raw_id is None or not isinstance(raw_name, str) or not raw_name.strip():
                    continue
                cleaned_name = raw_name.strip()
                category_names_by_id[str(raw_id)] = cleaned_name
                norm_source_raw = category_row.get("name_norm") or category_row.get("name") or category_row.get("system_key")
                norm_source = str(norm_source_raw).strip() if norm_source_raw else ""
                if norm_source:
                    for candidate_norm in _category_norm_candidates(norm_source):
                        category_names_by_norm[candidate_norm] = cleaned_name

        for row in rows:
            if request.group_by == RelevesGroupBy.CATEGORIE:
                category_id = row.get("category_id")
                key: str | None = category_names_by_id.get(str(category_id)) if category_id is not None else None

                raw_category = row.get("categorie")
                if key is None and isinstance(raw_category, str) and raw_category.strip():
                    normalized_candidates = _category_norm_candidates(raw_category)
                    key = next((category_names_by_norm.get(candidate) for candidate in normalized_candidates if category_names_by_norm.get(candidate)), None) or raw_category

                if key is None:
                    key = "Autres"
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

    def list_pending_categorization_releves(
        self,
        *,
        profile_id: UUID,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        query: list[tuple[str, str | int]] = [
            ("profile_id", f"eq.{profile_id}"),
            ("select", "id,date,montant,devise,libelle,payee,categorie,metadonnees"),
            ("limit", max(1, min(limit, 200))),
            ("offset", 0),
        ]
        rows, _ = self._client.get_rows(table="releves_bancaires", query=query, with_count=False)

        filtered_rows: list[dict[str, object]] = []
        for row in rows:
            if self._row_is_internal_transfer(row):
                continue

            meta = row.get("metadonnees") if isinstance(row.get("metadonnees"), dict) else {}
            category_status = str(meta.get("category_status") or "").strip().lower()
            category_key = str(meta.get("category_key") or "").strip().lower()
            if category_status != "pending" and category_key != "twint_p2p_pending":
                continue

            filtered_rows.append(
                {
                    "id": row.get("id"),
                    "date": row.get("date"),
                    "montant": row.get("montant"),
                    "devise": row.get("devise"),
                    "libelle": row.get("libelle"),
                    "payee": row.get("payee"),
                    "categorie": row.get("categorie"),
                    "meta": {
                        "category_key": meta.get("category_key"),
                        "category_status": meta.get("category_status"),
                    },
                }
            )

            if len(filtered_rows) >= limit:
                break

        return filtered_rows

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
                "merchant_entity_id": str(row["merchant_entity_id"]) if row.get("merchant_entity_id") else None,
                "category_id": str(row["category_id"]) if row.get("category_id") else None,
                "source": row.get("source"),
                "metadonnees": row.get("meta") if isinstance(row.get("meta"), dict) else {},
            }
            if row.get("contenu_brut") is not None:
                base_payload["contenu_brut"] = row.get("contenu_brut")
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
