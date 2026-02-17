"""Deterministic fakes for tool-router contract tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from shared.text_utils import normalize_category_name
from shared.models import (
    BankAccount,
    BankAccountsListResult,
    CategoriesListResult,
    ProfileCategory,
    ProfileDataResult,
    PROFILE_DEFAULT_CORE_FIELDS,
    ReleveBancaire,
    RelevesAggregateRequest,
    RelevesAggregateResult,
    RelevesDirection,
    RelevesFilters,
    RelevesGroupBy,
    RelevesSearchResult,
    RelevesSumResult,
    ToolError,
    ToolErrorCode,
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
    """Minimal backend client fake implementing releves and categories tools."""

    categories: list[ProfileCategory] = field(default_factory=list)
    bank_accounts: list[BankAccount] = field(default_factory=list)
    profile_data_by_id: dict[UUID, dict[str, object | None]] = field(default_factory=dict)

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

    def finance_categories_list(self, profile_id: UUID) -> CategoriesListResult:
        return CategoriesListResult(items=[item for item in self.categories if item.profile_id == profile_id])

    def finance_categories_create(
        self,
        *,
        profile_id: UUID,
        name: str,
        exclude_from_totals: bool = False,
    ) -> ProfileCategory:
        now = datetime.now(timezone.utc)
        category = ProfileCategory(
            id=UUID("44444444-4444-4444-4444-444444444444") if not self.categories else UUID("55555555-5555-5555-5555-555555555555"),
            profile_id=profile_id,
            name=name,
            name_norm=normalize_category_name(name),
            exclude_from_totals=exclude_from_totals,
            created_at=now,
            updated_at=now,
        )
        self.categories.append(category)
        return category

    def finance_categories_update(
        self,
        *,
        profile_id: UUID,
        category_id: UUID,
        name: str | None = None,
        exclude_from_totals: bool | None = None,
    ) -> ProfileCategory | ToolError:
        for index, item in enumerate(self.categories):
            if item.profile_id != profile_id or item.id != category_id:
                continue
            updated_name = name if name is not None else item.name
            updated = item.model_copy(
                update={
                    "name": updated_name,
                    "name_norm": normalize_category_name(updated_name),
                    "exclude_from_totals": (
                        exclude_from_totals
                        if exclude_from_totals is not None
                        else item.exclude_from_totals
                    ),
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            self.categories[index] = updated
            return updated
        return ToolError(code=ToolErrorCode.NOT_FOUND, message="Category not found")

    def finance_categories_delete(self, *, profile_id: UUID, category_id: UUID) -> dict[str, bool] | ToolError:
        for index, item in enumerate(self.categories):
            if item.profile_id == profile_id and item.id == category_id:
                self.categories.pop(index)
                return {"ok": True}
        return ToolError(code=ToolErrorCode.NOT_FOUND, message="Category not found")

    def finance_bank_accounts_list(self, *, profile_id: UUID) -> BankAccountsListResult:
        return BankAccountsListResult(
            items=[item for item in self.bank_accounts if item.profile_id == profile_id],
            default_bank_account_id=None,
        )

    def finance_bank_accounts_create(
        self,
        *,
        profile_id: UUID,
        name: str,
        kind: str | None = None,
        account_kind: str | None = None,
    ) -> BankAccount:
        account = BankAccount(
            id=UUID("66666666-6666-6666-6666-666666666666") if not self.bank_accounts else UUID("77777777-7777-7777-7777-777777777777"),
            profile_id=profile_id,
            name=name,
            kind=kind,
            account_kind=account_kind,
            is_system=False,
        )
        self.bank_accounts.append(account)
        return account

    def finance_bank_accounts_update(
        self,
        *,
        profile_id: UUID,
        bank_account_id: UUID,
        set_fields: dict[str, str],
    ) -> BankAccount | ToolError:
        for index, item in enumerate(self.bank_accounts):
            if item.profile_id != profile_id or item.id != bank_account_id:
                continue
            updated = item.model_copy(update=set_fields)
            self.bank_accounts[index] = updated
            return updated
        return ToolError(code=ToolErrorCode.NOT_FOUND, message="Bank account not found")

    def finance_bank_accounts_delete(self, *, profile_id: UUID, bank_account_id: UUID) -> dict[str, bool] | ToolError:
        for index, item in enumerate(self.bank_accounts):
            if item.profile_id == profile_id and item.id == bank_account_id:
                self.bank_accounts.pop(index)
                return {"ok": True}
        return ToolError(code=ToolErrorCode.NOT_FOUND, message="Bank account not found")

    def finance_bank_accounts_set_default(
        self,
        *,
        profile_id: UUID,
        bank_account_id: UUID,
    ) -> dict[str, object] | ToolError:
        for item in self.bank_accounts:
            if item.profile_id == profile_id and item.id == bank_account_id:
                return {"ok": True, "default_bank_account_id": str(bank_account_id)}
        return ToolError(code=ToolErrorCode.NOT_FOUND, message="Bank account not found")


    def finance_profile_get(
        self,
        *,
        profile_id: UUID,
        fields: list[str] | None = None,
    ) -> ProfileDataResult:
        selected_fields = fields or list(PROFILE_DEFAULT_CORE_FIELDS)
        profile_data = self.profile_data_by_id.get(profile_id, {})
        return ProfileDataResult(
            profile_id=profile_id,
            data={field: profile_data.get(field) for field in selected_fields},
        )

    def finance_profile_update(
        self,
        *,
        profile_id: UUID,
        set_fields: dict[str, object | None],
    ) -> ProfileDataResult:
        profile_data = self.profile_data_by_id.setdefault(profile_id, {})
        profile_data.update(set_fields)
        return ProfileDataResult(
            profile_id=profile_id,
            data={field: profile_data.get(field) for field in set_fields},
        )
