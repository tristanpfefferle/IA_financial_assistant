"""Deterministic fakes for tool-router contract tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from shared.models import (
    ReleveBancaire,
    RelevesAggregateRequest,
    RelevesAggregateResult,
    RelevesDirection,
    RelevesFilters,
    RelevesGroupBy,
    RelevesSearchResult,
    RelevesSumResult,
)


_FIXED_RELEVES = [
    ReleveBancaire(
        id=UUID("11111111-1111-1111-1111-111111111111"),
        date=date(2025, 1, 10),
        montant=Decimal("-12.30"),
        devise="CHF",
        payee="coffee shop",
    ),
    ReleveBancaire(
        id=UUID("22222222-2222-2222-2222-222222222222"),
        date=date(2025, 1, 11),
        montant=Decimal("-20.00"),
        devise="CHF",
        libelle="Migros",
    ),
    ReleveBancaire(
        id=UUID("33333333-3333-3333-3333-333333333333"),
        date=date(2025, 1, 12),
        montant=Decimal("1000.00"),
        devise="CHF",
        payee="Salaire",
    ),
]


@dataclass(slots=True)
class FakeBackendClient:
    """Minimal backend client fake implementing releves tools only."""

    def _filtered_items(self, filters: RelevesFilters) -> list[ReleveBancaire]:
        items = list(_FIXED_RELEVES)

        if filters.merchant:
            needle = filters.merchant.lower()
            items = [
                item
                for item in items
                if needle in (item.payee or "").lower() or needle in (item.libelle or "").lower()
            ]

        if filters.direction == RelevesDirection.DEBIT_ONLY:
            items = [item for item in items if item.montant < 0]
        elif filters.direction == RelevesDirection.CREDIT_ONLY:
            items = [item for item in items if item.montant > 0]

        if filters.date_range is not None:
            start_date = filters.date_range.start_date
            end_date = filters.date_range.end_date
            items = [item for item in items if start_date <= item.date <= end_date]

        if filters.categorie:
            categorie = filters.categorie.lower()
            items = [item for item in items if (item.categorie or "").lower() == categorie]

        return items

    def releves_search(self, filters: RelevesFilters) -> RelevesSearchResult:
        filtered = self._filtered_items(filters)
        paginated = filtered[filters.offset : filters.offset + filters.limit]
        return RelevesSearchResult(
            items=paginated,
            limit=filters.limit,
            offset=filters.offset,
            total=len(filtered),
        )

    def releves_sum(self, filters: RelevesFilters) -> RelevesSumResult:
        filtered = self._filtered_items(filters)
        count = len(filtered)
        total = sum((item.montant for item in filtered), start=Decimal("0"))
        average = total / count if count > 0 else Decimal("0")
        return RelevesSumResult(total=total, count=count, average=average, currency="CHF", filters=filters)


    def releves_aggregate(self, request: RelevesAggregateRequest) -> RelevesAggregateResult:
        filters = RelevesFilters(
            profile_id=request.profile_id,
            date_range=request.date_range,
            categorie=request.categorie,
            merchant=request.merchant,
            merchant_id=request.merchant_id,
            direction=request.direction,
            limit=500,
            offset=0,
        )
        filtered = self._filtered_items(filters)

        groups: dict[str, dict[str, object]] = {}
        for item in filtered:
            if request.group_by == RelevesGroupBy.CATEGORIE:
                key = item.categorie or "Non catégorisé"
            elif request.group_by == RelevesGroupBy.PAYEE:
                key = item.payee or item.libelle or "Inconnu"
            else:
                key = item.date.strftime("%Y-%m")

            current = groups.setdefault(key, {"total": Decimal("0"), "count": 0})
            current["total"] = current["total"] + item.montant
            current["count"] = current["count"] + 1

        return RelevesAggregateResult(
            group_by=request.group_by,
            groups={
                name: {"total": values["total"], "count": values["count"]}
                for name, values in groups.items()
            },
            currency="CHF",
            filters=request,
        )
