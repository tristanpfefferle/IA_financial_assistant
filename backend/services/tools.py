"""Backend tool service placeholders.

All business logic must stay in backend and should be delegated to the existing
`gestion_financiere` repository via wrappers.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import ValidationError

from backend.repositories.bank_accounts_repository import BankAccountsRepository
from backend.repositories.categories_repository import CategoriesRepository
from backend.repositories.profiles_repository import ProfilesRepository
from backend.repositories.releves_repository import RelevesRepository
from backend.repositories.transactions_repository import TransactionsRepository
from backend.services.releves_import import RelevesImportService
from shared.models import (
    BankAccount,
    BankAccountCreateRequest,
    BankAccountDeleteRequest,
    BankAccountSetDefaultRequest,
    BankAccountsListResult,
    BankAccountUpdateRequest,
    CategoriesListResult,
    CategoryCreateRequest,
    CategoryDeleteRequest,
    CategoryUpdateRequest,
    ProfileCategory,
    ProfileDataResult,
    RelevesAggregateGroup,
    RelevesAggregateRequest,
    RelevesAggregateResult,
    RelevesFilters,
    RelevesImportRequest,
    RelevesImportResult,
    RelevesSearchResult,
    RelevesSumResult,
    ToolError,
    ToolErrorCode,
    TransactionFilters,
    TransactionSearchResult,
    TransactionSumResult,
)


@dataclass(slots=True)
class BackendToolService:
    transactions_repository: TransactionsRepository
    releves_repository: RelevesRepository
    categories_repository: CategoriesRepository
    bank_accounts_repository: BankAccountsRepository | None = None
    profiles_repository: ProfilesRepository | None = None

    def finance_releves_import_files(
        self,
        *,
        request: RelevesImportRequest,
    ) -> RelevesImportResult | ToolError:
        try:
            service = RelevesImportService(releves_repository=self.releves_repository)
            return service.import_releves(request)
        except Exception as exc:
            return ToolError(code=ToolErrorCode.BACKEND_ERROR, message=str(exc))

    def search_transactions(
        self, filters: TransactionFilters
    ) -> TransactionSearchResult | ToolError:
        """Deprecated alias for releves_search kept for compatibility."""

        return self.releves_search(filters)

    def sum_transactions(
        self, filters: TransactionFilters
    ) -> TransactionSumResult | ToolError:
        """Deprecated alias for releves_sum kept for compatibility."""

        return self.releves_sum(filters)

    def releves_search(
        self, filters: RelevesFilters
    ) -> RelevesSearchResult | ToolError:
        try:
            items, total = self.releves_repository.list_releves(filters)
            return RelevesSearchResult(
                items=items, limit=filters.limit, offset=filters.offset, total=total
            )
        except Exception as exc:  # placeholder normalization at contract boundary
            return ToolError(code=ToolErrorCode.BACKEND_ERROR, message=str(exc))

    def releves_sum(self, filters: RelevesFilters) -> RelevesSumResult | ToolError:
        try:
            total, count, currency = self.releves_repository.sum_releves(filters)
            average = (total / count) if count > 0 else total
            return RelevesSumResult(
                total=total,
                count=count,
                average=average,
                currency=currency,
                filters=filters,
            )
        except Exception as exc:  # placeholder normalization at contract boundary
            return ToolError(code=ToolErrorCode.BACKEND_ERROR, message=str(exc))

    def releves_aggregate(
        self, request: RelevesAggregateRequest
    ) -> RelevesAggregateResult | ToolError:
        try:
            aggregated, currency = self.releves_repository.aggregate_releves(request)
            groups = {
                group_key: RelevesAggregateGroup(total=total, count=count)
                for group_key, (total, count) in aggregated.items()
            }
            return RelevesAggregateResult(
                group_by=request.group_by,
                groups=groups,
                currency=currency,
                filters=request,
            )
        except Exception as exc:  # placeholder normalization at contract boundary
            return ToolError(code=ToolErrorCode.BACKEND_ERROR, message=str(exc))


    def finance_releves_set_bank_account(
        self,
        *,
        profile_id: UUID,
        bank_account_id: UUID,
        filters: dict[str, object] | None = None,
        releve_ids: list[UUID] | None = None,
    ) -> dict[str, object] | ToolError:
        if self.bank_accounts_repository is None:
            return ToolError(
                code=ToolErrorCode.BACKEND_ERROR,
                message="Bank accounts repository unavailable",
            )

        try:
            accounts = self.bank_accounts_repository.list_bank_accounts(profile_id=profile_id)
        except Exception as exc:
            return ToolError(code=ToolErrorCode.BACKEND_ERROR, message=str(exc))

        if not any(account.id == bank_account_id for account in accounts):
            return ToolError(code=ToolErrorCode.NOT_FOUND, message="Bank account not found")

        try:
            if releve_ids is not None:
                updated_count = self.releves_repository.update_bank_account_id_by_ids(
                    profile_id=profile_id,
                    releve_ids=releve_ids,
                    bank_account_id=bank_account_id,
                )
                return {"ok": True, "updated_count": updated_count}

            if filters is None:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message="Either releve_ids or filters must be provided",
                )

            releves_filters = RelevesFilters.model_validate({
                **filters,
                "profile_id": str(profile_id),
            })
            updated_count = self.releves_repository.update_bank_account_id_by_filters(
                profile_id=profile_id,
                filters=releves_filters,
                bank_account_id=bank_account_id,
            )
            return {"ok": True, "updated_count": updated_count}
        except ValidationError as exc:
            return ToolError(
                code=ToolErrorCode.VALIDATION_ERROR,
                message="Invalid payload for finance_releves_set_bank_account",
                details={"validation_errors": exc.errors()},
            )
        except Exception as exc:
            return ToolError(code=ToolErrorCode.BACKEND_ERROR, message=str(exc))

    def finance_categories_list(
        self, profile_id: UUID
    ) -> CategoriesListResult | ToolError:
        try:
            items = self.categories_repository.list_categories(profile_id=profile_id)
            return CategoriesListResult(items=items)
        except Exception as exc:  # placeholder normalization at contract boundary
            return ToolError(code=ToolErrorCode.BACKEND_ERROR, message=str(exc))

    def finance_categories_create(
        self,
        profile_id: UUID,
        name: str,
        exclude_from_totals: bool = False,
    ) -> ProfileCategory | ToolError:
        try:
            return self.categories_repository.create_category(
                CategoryCreateRequest(
                    profile_id=profile_id,
                    name=name,
                    exclude_from_totals=exclude_from_totals,
                )
            )
        except Exception as exc:  # placeholder normalization at contract boundary
            return ToolError(code=ToolErrorCode.BACKEND_ERROR, message=str(exc))

    def finance_categories_update(
        self,
        profile_id: UUID,
        category_id: UUID,
        name: str | None = None,
        exclude_from_totals: bool | None = None,
    ) -> ProfileCategory | ToolError:
        try:
            return self.categories_repository.update_category(
                CategoryUpdateRequest(
                    profile_id=profile_id,
                    category_id=category_id,
                    name=name,
                    exclude_from_totals=exclude_from_totals,
                )
            )
        except ValueError as exc:
            return ToolError(code=ToolErrorCode.NOT_FOUND, message=str(exc))
        except Exception as exc:  # placeholder normalization at contract boundary
            return ToolError(code=ToolErrorCode.BACKEND_ERROR, message=str(exc))

    def finance_categories_delete(
        self, profile_id: UUID, category_id: UUID
    ) -> dict[str, bool] | ToolError:
        try:
            self.categories_repository.delete_category(
                CategoryDeleteRequest(profile_id=profile_id, category_id=category_id)
            )
            return {"ok": True}
        except ValueError as exc:
            return ToolError(code=ToolErrorCode.NOT_FOUND, message=str(exc))
        except Exception as exc:  # placeholder normalization at contract boundary
            return ToolError(code=ToolErrorCode.BACKEND_ERROR, message=str(exc))

    def finance_profile_get(
        self,
        profile_id: UUID,
        fields: list[str] | None = None,
    ) -> ProfileDataResult | ToolError:
        if self.profiles_repository is None:
            return ToolError(
                code=ToolErrorCode.BACKEND_ERROR,
                message="Profiles repository unavailable",
            )

        try:
            data = self.profiles_repository.get_profile_fields(
                profile_id=profile_id, fields=fields
            )
            return ProfileDataResult(profile_id=profile_id, data=data)
        except ValueError as exc:
            return ToolError(code=ToolErrorCode.NOT_FOUND, message=str(exc))
        except Exception as exc:  # placeholder normalization at contract boundary
            return ToolError(code=ToolErrorCode.BACKEND_ERROR, message=str(exc))

    def finance_profile_update(
        self,
        profile_id: UUID,
        set_fields: dict[str, object | None],
    ) -> ProfileDataResult | ToolError:
        if self.profiles_repository is None:
            return ToolError(
                code=ToolErrorCode.BACKEND_ERROR,
                message="Profiles repository unavailable",
            )

        try:
            updated_data = self.profiles_repository.update_profile_fields(
                profile_id=profile_id,
                set_dict=set_fields,
            )
            return ProfileDataResult(profile_id=profile_id, data=updated_data)
        except ValueError as exc:
            return ToolError(code=ToolErrorCode.NOT_FOUND, message=str(exc))
        except Exception as exc:  # placeholder normalization at contract boundary
            return ToolError(code=ToolErrorCode.BACKEND_ERROR, message=str(exc))

    def finance_bank_accounts_list(
        self, profile_id: UUID
    ) -> BankAccountsListResult | ToolError:
        if self.bank_accounts_repository is None:
            return ToolError(
                code=ToolErrorCode.BACKEND_ERROR,
                message="Bank accounts repository unavailable",
            )
        try:
            items = self.bank_accounts_repository.list_bank_accounts(
                profile_id=profile_id
            )
            default_id: UUID | None = None

            if self.profiles_repository is not None:
                try:
                    data = self.profiles_repository.get_profile_fields(
                        profile_id=profile_id,
                        fields=["default_bank_account_id"],
                    )
                except ValueError as exc:
                    return ToolError(code=ToolErrorCode.NOT_FOUND, message=str(exc))

                raw = data.get("default_bank_account_id")
                if raw:
                    try:
                        default_id = UUID(str(raw))
                    except ValueError:
                        default_id = None

            return BankAccountsListResult(
                items=items, default_bank_account_id=default_id
            )
        except Exception as exc:
            return ToolError(code=ToolErrorCode.BACKEND_ERROR, message=str(exc))

    def finance_bank_accounts_create(
        self,
        profile_id: UUID,
        name: str,
        kind: str | None = None,
        account_kind: str | None = None,
    ) -> BankAccount | ToolError:
        if self.bank_accounts_repository is None:
            return ToolError(
                code=ToolErrorCode.BACKEND_ERROR,
                message="Bank accounts repository unavailable",
            )
        try:
            return self.bank_accounts_repository.create_bank_account(
                BankAccountCreateRequest(
                    profile_id=profile_id,
                    name=name,
                    kind=kind,
                    account_kind=account_kind,
                )
            )
        except ValueError as exc:
            if str(exc) == "bank account name already exists":
                return ToolError(code=ToolErrorCode.CONFLICT, message=str(exc))
            return ToolError(code=ToolErrorCode.BACKEND_ERROR, message=str(exc))
        except Exception as exc:
            return ToolError(code=ToolErrorCode.BACKEND_ERROR, message=str(exc))

    def finance_bank_accounts_update(
        self,
        profile_id: UUID,
        bank_account_id: UUID,
        set_fields: dict[str, str],
    ) -> BankAccount | ToolError:
        if self.bank_accounts_repository is None:
            return ToolError(
                code=ToolErrorCode.BACKEND_ERROR,
                message="Bank accounts repository unavailable",
            )
        try:
            return self.bank_accounts_repository.update_bank_account(
                BankAccountUpdateRequest(
                    profile_id=profile_id,
                    bank_account_id=bank_account_id,
                    set=set_fields,
                )
            )
        except ValueError as exc:
            return ToolError(code=ToolErrorCode.NOT_FOUND, message=str(exc))
        except Exception as exc:
            return ToolError(code=ToolErrorCode.BACKEND_ERROR, message=str(exc))

    def finance_bank_accounts_delete(
        self, profile_id: UUID, bank_account_id: UUID
    ) -> dict[str, bool] | ToolError:
        if self.bank_accounts_repository is None:
            return ToolError(
                code=ToolErrorCode.BACKEND_ERROR,
                message="Bank accounts repository unavailable",
            )
        try:
            self.bank_accounts_repository.delete_bank_account(
                BankAccountDeleteRequest(
                    profile_id=profile_id, bank_account_id=bank_account_id
                )
            )
            return {"ok": True}
        except ValueError as exc:
            if str(exc) == "bank account not empty":
                return ToolError(code=ToolErrorCode.CONFLICT, message=str(exc))
            return ToolError(code=ToolErrorCode.NOT_FOUND, message=str(exc))
        except Exception as exc:
            return ToolError(code=ToolErrorCode.BACKEND_ERROR, message=str(exc))

    def finance_bank_accounts_can_delete(
        self,
        profile_id: UUID,
        bank_account_id: UUID,
    ) -> dict[str, object] | ToolError:
        if self.bank_accounts_repository is None:
            return ToolError(
                code=ToolErrorCode.BACKEND_ERROR,
                message="Bank accounts repository unavailable",
            )
        try:
            can_delete = self.bank_accounts_repository.can_delete_bank_account(
                BankAccountDeleteRequest(
                    profile_id=profile_id,
                    bank_account_id=bank_account_id,
                )
            )
            if can_delete:
                return {"ok": True, "can_delete": True}
            return {"ok": True, "can_delete": False, "reason": "not_empty"}
        except Exception as exc:
            return ToolError(code=ToolErrorCode.BACKEND_ERROR, message=str(exc))

    def finance_bank_accounts_set_default(
        self,
        profile_id: UUID,
        bank_account_id: UUID,
    ) -> dict[str, object] | ToolError:
        if self.bank_accounts_repository is None:
            return ToolError(
                code=ToolErrorCode.BACKEND_ERROR,
                message="Bank accounts repository unavailable",
            )
        try:
            default_bank_account_id = (
                self.bank_accounts_repository.set_default_bank_account(
                    BankAccountSetDefaultRequest(
                        profile_id=profile_id, bank_account_id=bank_account_id
                    )
                )
            )
            return {"ok": True, "default_bank_account_id": str(default_bank_account_id)}
        except ValueError as exc:
            return ToolError(code=ToolErrorCode.NOT_FOUND, message=str(exc))
        except Exception as exc:
            return ToolError(code=ToolErrorCode.BACKEND_ERROR, message=str(exc))

    def finance_merchants_rename(
        self,
        *,
        profile_id: UUID,
        merchant_id: UUID,
        name: str,
    ) -> dict[str, str] | ToolError:
        if self.profiles_repository is None:
            return ToolError(
                code=ToolErrorCode.BACKEND_ERROR,
                message="Profiles repository unavailable",
            )
        try:
            return self.profiles_repository.rename_merchant(
                profile_id=profile_id,
                merchant_id=merchant_id,
                new_name=name,
            )
        except ValueError as exc:
            code = ToolErrorCode.NOT_FOUND if "not found" in str(exc).lower() else ToolErrorCode.VALIDATION_ERROR
            return ToolError(code=code, message=str(exc))
        except Exception as exc:
            return ToolError(code=ToolErrorCode.BACKEND_ERROR, message=str(exc))

    def finance_merchants_merge(
        self,
        *,
        profile_id: UUID,
        source_merchant_id: UUID,
        target_merchant_id: UUID,
    ) -> dict[str, object] | ToolError:
        if self.profiles_repository is None:
            return ToolError(
                code=ToolErrorCode.BACKEND_ERROR,
                message="Profiles repository unavailable",
            )
        try:
            return self.profiles_repository.merge_merchants(
                profile_id=profile_id,
                source_merchant_id=source_merchant_id,
                target_merchant_id=target_merchant_id,
            )
        except ValueError as exc:
            code = ToolErrorCode.NOT_FOUND if "not found" in str(exc).lower() else ToolErrorCode.VALIDATION_ERROR
            return ToolError(code=code, message=str(exc))
        except Exception as exc:
            return ToolError(code=ToolErrorCode.BACKEND_ERROR, message=str(exc))

    def finance_merchants_suggest_fixes(
        self,
        *,
        profile_id: UUID,
        status: str = "pending",
        limit: int = 50,
    ) -> dict[str, object] | ToolError:
        if self.profiles_repository is None:
            return ToolError(
                code=ToolErrorCode.BACKEND_ERROR,
                message="Profiles repository unavailable",
            )
        try:
            items = self.profiles_repository.list_merchant_suggestions(
                profile_id=profile_id,
                status=status,
                limit=limit,
            )
            return {"items": items, "count": len(items)}
        except Exception as exc:
            return ToolError(code=ToolErrorCode.BACKEND_ERROR, message=str(exc))

    def finance_merchants_apply_suggestion(
        self,
        *,
        profile_id: UUID,
        suggestion_id: UUID,
    ) -> dict[str, object] | ToolError:
        if self.profiles_repository is None:
            return ToolError(
                code=ToolErrorCode.BACKEND_ERROR,
                message="Profiles repository unavailable",
            )

        try:
            suggestion = self.profiles_repository.get_merchant_suggestion_by_id(
                profile_id=profile_id,
                suggestion_id=suggestion_id,
            )
            if not suggestion:
                return ToolError(code=ToolErrorCode.NOT_FOUND, message="Merchant suggestion not found")

            action = str(suggestion.get("action") or "").strip().lower()
            applied_details: dict[str, object] = {}

            if action == "rename":
                source_merchant_id = UUID(str(suggestion.get("source_merchant_id")))
                suggested_name = str(suggestion.get("suggested_name") or "").strip()
                applied_details = self.profiles_repository.rename_merchant(
                    profile_id=profile_id,
                    merchant_id=source_merchant_id,
                    new_name=suggested_name,
                )
            elif action == "merge":
                source_merchant_id = UUID(str(suggestion.get("source_merchant_id")))
                target_merchant_id = UUID(str(suggestion.get("target_merchant_id")))
                applied_details = self.profiles_repository.merge_merchants(
                    profile_id=profile_id,
                    source_merchant_id=source_merchant_id,
                    target_merchant_id=target_merchant_id,
                )
            elif action == "categorize":
                source_merchant_id = UUID(str(suggestion.get("source_merchant_id")))
                suggested_category = str(suggestion.get("suggested_category") or "").strip()
                self.profiles_repository.update_merchant_category(
                    merchant_id=source_merchant_id,
                    category_name=suggested_category,
                )
                applied_details = {
                    "merchant_id": str(source_merchant_id),
                    "category": suggested_category,
                }
            elif action == "keep":
                applied_details = {"noop": True}
            else:
                raise ValueError(f"unsupported action: {action}")

            self.profiles_repository.update_merchant_suggestion_status(
                profile_id=profile_id,
                suggestion_id=suggestion_id,
                status="applied",
                error=None,
            )
            return {
                "ok": True,
                "action": action,
                "suggestion_id": str(suggestion_id),
                "applied_details": applied_details,
            }
        except Exception as exc:
            try:
                self.profiles_repository.update_merchant_suggestion_status(
                    profile_id=profile_id,
                    suggestion_id=suggestion_id,
                    status="failed",
                    error=str(exc),
                )
            except Exception:
                pass
            return ToolError(code=ToolErrorCode.BACKEND_ERROR, message=str(exc))
