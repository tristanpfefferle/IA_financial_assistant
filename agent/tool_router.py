"""Minimal tool router for mapping tool names to backend client methods."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import get_close_matches
from uuid import UUID

from shared.text_utils import normalize_category_name
from shared.profile_fields import normalize_profile_field
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from agent.backend_client import BackendClient
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
    ProfileGetRequest,
    ProfileUpdateRequest,
    RelevesAggregateRequest,
    RelevesAggregateResult,
    RelevesFilters,
    RelevesImportRequest,
    RelevesImportResult,
    RelevesSearchResult,
    RelevesSumResult,
    ToolError,
    ToolErrorCode,
)


class _CategoryUpdateByNamePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: UUID | None = None
    category_name: str | None = None
    name: str | None = None
    exclude_from_totals: bool | None = None

    @model_validator(mode="after")
    def validate_identifier(self) -> "_CategoryUpdateByNamePayload":
        if self.category_id is None and (self.category_name is None or not self.category_name.strip()):
            raise ValueError("Either category_id or category_name must be provided")
        return self


class _CategoryDeleteByNamePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: UUID | None = None
    category_name: str | None = None

    @model_validator(mode="after")
    def validate_identifier(self) -> "_CategoryDeleteByNamePayload":
        if self.category_id is None and (self.category_name is None or not self.category_name.strip()):
            raise ValueError("Either category_id or category_name must be provided")
        return self


class _BankAccountUpdateByNamePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bank_account_id: UUID | None = None
    name: str | None = None
    set: dict[str, str]

    @model_validator(mode="after")
    def validate_identifier(self) -> "_BankAccountUpdateByNamePayload":
        if self.bank_account_id is None and (self.name is None or not self.name.strip()):
            raise ValueError("Either bank_account_id or name must be provided")
        return self


class _BankAccountDeleteByNamePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bank_account_id: UUID | None = None
    name: str | None = None

    @model_validator(mode="after")
    def validate_identifier(self) -> "_BankAccountDeleteByNamePayload":
        if self.bank_account_id is None and (self.name is None or not self.name.strip()):
            raise ValueError("Either bank_account_id or name must be provided")
        return self


class _BankAccountSetDefaultByNamePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bank_account_id: UUID | None = None
    name: str | None = None

    @model_validator(mode="after")
    def validate_identifier(self) -> "_BankAccountSetDefaultByNamePayload":
        if self.bank_account_id is None and (self.name is None or not self.name.strip()):
            raise ValueError("Either bank_account_id or name must be provided")
        return self


class _RelevesSetBankAccountPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bank_account_id: UUID | None = None
    bank_account_name: str | None = None
    filters: dict[str, object] | None = None
    releve_ids: list[UUID] | None = None
    releves_ids: list[UUID] | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> "_RelevesSetBankAccountPayload":
        has_account_identifier = self.bank_account_id is not None or (
            self.bank_account_name is not None and self.bank_account_name.strip()
        )
        if not has_account_identifier:
            raise ValueError("Either bank_account_id or bank_account_name must be provided")

        has_ids = bool(self.releve_ids or self.releves_ids)
        if not has_ids and self.filters is None:
            raise ValueError("Either releve_ids/releves_ids or filters must be provided")

        if has_ids and self.filters is not None:
            raise ValueError("Provide either releve_ids/releves_ids or filters, not both")

        return self


class _RelevesImportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[dict[str, str]]
    bank_account_id: UUID | None = None
    import_mode: str = "analyze"
    modified_action: str = "keep"


class _MerchantRenamePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merchant_id: UUID
    name: str


class _MerchantsMergePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_merchant_id: UUID
    target_merchant_id: UUID


class _MerchantsSuggestFixesPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "pending"
    limit: int = 50


class _MerchantsApplySuggestionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggestion_id: UUID


@dataclass(slots=True)
class ToolRouter:
    backend_client: BackendClient

    def _find_category_by_name(
        self,
        *,
        profile_id: UUID,
        category_name: str,
    ) -> ProfileCategory | ToolError:
        categories_result = self.backend_client.finance_categories_list(profile_id=profile_id)
        if isinstance(categories_result, ToolError):
            return categories_result

        target_name = normalize_category_name(category_name)
        exact_matches = [item for item in categories_result.items if item.name_norm == target_name]

        if len(exact_matches) == 1:
            return exact_matches[0]

        if len(exact_matches) > 1:
            return ToolError(
                code=ToolErrorCode.AMBIGUOUS,
                message="Multiple categories match the provided name.",
                details={
                    "category_name": category_name,
                    "category_name_norm": target_name,
                    "candidates": [item.name for item in exact_matches],
                },
            )

        close_name_norms = get_close_matches(
            target_name,
            [item.name_norm for item in categories_result.items],
            n=3,
            cutoff=0.6,
        )
        close_name_norms_set = set(close_name_norms)
        close_category_names = [
            item.name for item in categories_result.items if item.name_norm in close_name_norms_set
        ]
        return ToolError(
            code=ToolErrorCode.NOT_FOUND,
            message="Category not found for provided name.",
            details={
                "category_name": category_name,
                "category_name_norm": target_name,
                "close_category_names": close_category_names,
            },
        )

    def _find_bank_account_by_name(
        self,
        *,
        profile_id: UUID,
        name: str,
    ) -> BankAccount | ToolError:
        accounts_result = self.backend_client.finance_bank_accounts_list(profile_id=profile_id)
        if isinstance(accounts_result, ToolError):
            return accounts_result

        target_name = name.strip().lower()
        exact_matches = [item for item in accounts_result.items if item.name.strip().lower() == target_name]
        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1:
            return ToolError(
                code=ToolErrorCode.AMBIGUOUS,
                message="Multiple bank accounts match the provided name.",
                details={
                    "name": name,
                    "candidates": [
                        {"id": str(item.id), "name": item.name}
                        for item in exact_matches
                    ],
                },
            )

        candidate_names_by_norm: dict[str, str] = {}
        for account in accounts_result.items:
            normalized_account_name = account.name.strip().lower()
            if normalized_account_name and normalized_account_name not in candidate_names_by_norm:
                candidate_names_by_norm[normalized_account_name] = account.name

        close_name_norms = get_close_matches(
            target_name,
            list(candidate_names_by_norm.keys()),
            n=3,
            cutoff=0.6,
        )
        return ToolError(
            code=ToolErrorCode.NOT_FOUND,
            message="Bank account not found for provided name.",
            details={
                "name": name,
                "close_names": [candidate_names_by_norm[normalized_name] for normalized_name in close_name_norms],
            },
        )



    @staticmethod
    def _normalize_profile_get_payload(payload: dict) -> dict | ToolError:
        raw_fields = payload.get("fields")
        if raw_fields is None:
            return payload

        if not isinstance(raw_fields, list):
            return ToolError(
                code=ToolErrorCode.VALIDATION_ERROR,
                message="Invalid payload for tool finance_profile_get",
                details={"payload": payload},
            )

        normalized_fields: list[str] = []
        for raw_field in raw_fields:
            if not isinstance(raw_field, str):
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message="Invalid payload for tool finance_profile_get",
                    details={"payload": payload},
                )

            normalized = normalize_profile_field(raw_field)
            if isinstance(normalized, ToolError):
                return normalized
            normalized_fields.append(normalized)

        return {**payload, "fields": normalized_fields}

    @staticmethod
    def _normalize_profile_update_payload(payload: dict) -> dict | ToolError:
        raw_set = payload.get("set")
        if not isinstance(raw_set, dict):
            return payload

        normalized_set: dict[str, object | None] = {}
        for raw_field, value in raw_set.items():
            if not isinstance(raw_field, str):
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message="Invalid payload for tool finance_profile_update",
                    details={"payload": payload},
                )
            normalized = normalize_profile_field(raw_field)
            if isinstance(normalized, ToolError):
                return normalized
            normalized_set[normalized] = value

        return {**payload, "set": normalized_set}

    def call(
        self,
        tool_name: str,
        payload: dict,
        *,
        profile_id: UUID | None = None,
    ) -> (
        RelevesSearchResult
        | RelevesSumResult
        | RelevesAggregateResult
        | RelevesImportResult
        | CategoriesListResult
        | BankAccountsListResult
        | ProfileCategory
        | BankAccount
        | dict[str, bool]
        | dict[str, object]
        | ProfileDataResult
        | ToolError
    ):
        if tool_name in {
            "finance_transactions_search",
            "finance_releves_search",
            "finance_transactions_sum",
            "finance_releves_sum",
            "finance_releves_aggregate",
            "finance_releves_set_bank_account",
            "finance_releves_import_files",
            "finance_categories_list",
            "finance_categories_create",
            "finance_categories_update",
            "finance_categories_delete",
            "finance_profile_get",
            "finance_profile_update",
            "finance_bank_accounts_list",
            "finance_bank_accounts_create",
            "finance_bank_accounts_update",
            "finance_bank_accounts_delete",
            "finance_bank_accounts_can_delete",
            "finance_bank_accounts_set_default",
            "finance_merchants_rename",
            "finance_merchants_merge",
            "finance_merchants_suggest_fixes",
            "finance_merchants_apply_suggestion",
        } and profile_id is None:
            return ToolError(
                code=ToolErrorCode.VALIDATION_ERROR,
                message=f"Missing profile_id context for tool {tool_name}",
            )

        if profile_id is not None and not isinstance(profile_id, UUID):
            try:
                profile_id = UUID(str(profile_id))
            except ValueError:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Invalid profile_id context for tool {tool_name}",
                )

        if tool_name in {"finance_transactions_search", "finance_releves_search"}:
            try:
                filters = RelevesFilters.model_validate({**payload, "profile_id": str(profile_id)})
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Invalid payload for tool {tool_name}",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )
            return self.backend_client.releves_search(filters)

        if tool_name in {"finance_transactions_sum", "finance_releves_sum"}:
            try:
                filters = RelevesFilters.model_validate({**payload, "profile_id": str(profile_id)})
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Invalid payload for tool {tool_name}",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )
            return self.backend_client.releves_sum(filters)

        if tool_name == "finance_releves_aggregate":
            try:
                request = RelevesAggregateRequest.model_validate(
                    {**payload, "profile_id": str(profile_id)}
                )
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Invalid payload for tool {tool_name}",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )
            return self.backend_client.releves_aggregate(request)

        if tool_name == "finance_releves_set_bank_account":
            try:
                request_payload = _RelevesSetBankAccountPayload.model_validate(payload)
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Invalid payload for tool {tool_name}",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )

            bank_account_id = request_payload.bank_account_id
            if bank_account_id is None:
                matched = self._find_bank_account_by_name(
                    profile_id=profile_id,
                    name=request_payload.bank_account_name or "",
                )
                if isinstance(matched, ToolError):
                    return matched
                bank_account_id = matched.id

            releve_ids = request_payload.releve_ids or request_payload.releves_ids
            return self.backend_client.finance_releves_set_bank_account(
                profile_id=profile_id,
                bank_account_id=bank_account_id,
                filters=request_payload.filters,
                releve_ids=releve_ids,
            )

        if tool_name == "finance_releves_import_files":
            try:
                request_payload = _RelevesImportPayload.model_validate(payload)
                request = RelevesImportRequest.model_validate(
                    {
                        **request_payload.model_dump(),
                        "profile_id": str(profile_id),
                    }
                )
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Invalid payload for tool {tool_name}",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )
            return self.backend_client.finance_releves_import_files(request=request)

        if tool_name == "finance_categories_list":
            if payload:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Invalid payload for tool {tool_name}",
                    details={"payload": payload},
                )
            return self.backend_client.finance_categories_list(profile_id=profile_id)

        if tool_name == "finance_categories_create":
            try:
                request = CategoryCreateRequest.model_validate({**payload, "profile_id": str(profile_id)})
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Invalid payload for tool {tool_name}",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )
            return self.backend_client.finance_categories_create(
                profile_id=request.profile_id,
                name=request.name,
                exclude_from_totals=request.exclude_from_totals,
            )

        if tool_name == "finance_categories_update":
            try:
                request_payload = _CategoryUpdateByNamePayload.model_validate(payload)
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Invalid payload for tool {tool_name}",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )

            category_id = request_payload.category_id
            if category_id is None:
                matched = self._find_category_by_name(
                    profile_id=profile_id,
                    category_name=request_payload.category_name or "",
                )
                if isinstance(matched, ToolError):
                    return matched
                category_id = matched.id

            try:
                request = CategoryUpdateRequest.model_validate(
                    {
                        "profile_id": str(profile_id),
                        "category_id": str(category_id),
                        "name": request_payload.name,
                        "exclude_from_totals": request_payload.exclude_from_totals,
                    }
                )
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Invalid payload for tool {tool_name}",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )

            return self.backend_client.finance_categories_update(
                profile_id=request.profile_id,
                category_id=request.category_id,
                name=request.name,
                exclude_from_totals=request.exclude_from_totals,
            )


        if tool_name == "finance_profile_get":
            normalized_payload = self._normalize_profile_get_payload(payload)
            if isinstance(normalized_payload, ToolError):
                return normalized_payload
            try:
                request = ProfileGetRequest.model_validate(normalized_payload)
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Invalid payload for tool {tool_name}",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )
            return self.backend_client.finance_profile_get(
                profile_id=profile_id,
                fields=request.fields,
            )

        if tool_name == "finance_profile_update":
            normalized_payload = self._normalize_profile_update_payload(payload)
            if isinstance(normalized_payload, ToolError):
                return normalized_payload
            try:
                request = ProfileUpdateRequest.model_validate(normalized_payload)
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Invalid payload for tool {tool_name}",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )
            return self.backend_client.finance_profile_update(
                profile_id=profile_id,
                set_fields=request.set,
            )
        if tool_name == "finance_categories_delete":
            try:
                request_payload = _CategoryDeleteByNamePayload.model_validate(payload)
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Invalid payload for tool {tool_name}",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )

            category_id = request_payload.category_id
            if category_id is None:
                matched = self._find_category_by_name(
                    profile_id=profile_id,
                    category_name=request_payload.category_name or "",
                )
                if isinstance(matched, ToolError):
                    return matched
                category_id = matched.id

            try:
                request = CategoryDeleteRequest.model_validate(
                    {"profile_id": str(profile_id), "category_id": str(category_id)}
                )
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Invalid payload for tool {tool_name}",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )
            return self.backend_client.finance_categories_delete(
                profile_id=request.profile_id,
                category_id=request.category_id,
            )

        if tool_name == "finance_bank_accounts_list":
            if payload:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Invalid payload for tool {tool_name}",
                    details={"payload": payload},
                )
            return self.backend_client.finance_bank_accounts_list(profile_id=profile_id)

        if tool_name == "finance_bank_accounts_create":
            try:
                request = BankAccountCreateRequest.model_validate({**payload, "profile_id": str(profile_id)})
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Invalid payload for tool {tool_name}",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )
            return self.backend_client.finance_bank_accounts_create(
                profile_id=request.profile_id,
                name=request.name,
                kind=request.kind,
                account_kind=request.account_kind,
            )

        if tool_name == "finance_bank_accounts_update":
            try:
                request_payload = _BankAccountUpdateByNamePayload.model_validate(payload)
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Invalid payload for tool {tool_name}",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )
            bank_account_id = request_payload.bank_account_id
            if bank_account_id is None:
                matched = self._find_bank_account_by_name(profile_id=profile_id, name=request_payload.name or "")
                if isinstance(matched, ToolError):
                    return matched
                bank_account_id = matched.id
            try:
                request = BankAccountUpdateRequest.model_validate(
                    {
                        "profile_id": str(profile_id),
                        "bank_account_id": str(bank_account_id),
                        "set": request_payload.set,
                    }
                )
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Invalid payload for tool {tool_name}",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )
            return self.backend_client.finance_bank_accounts_update(
                profile_id=request.profile_id,
                bank_account_id=request.bank_account_id,
                set_fields=request.set,
            )

        if tool_name == "finance_bank_accounts_delete":
            try:
                request_payload = _BankAccountDeleteByNamePayload.model_validate(payload)
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Invalid payload for tool {tool_name}",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )
            bank_account_id = request_payload.bank_account_id
            if bank_account_id is None:
                matched = self._find_bank_account_by_name(profile_id=profile_id, name=request_payload.name or "")
                if isinstance(matched, ToolError):
                    return matched
                bank_account_id = matched.id
            try:
                request = BankAccountDeleteRequest.model_validate(
                    {"profile_id": str(profile_id), "bank_account_id": str(bank_account_id)}
                )
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Invalid payload for tool {tool_name}",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )
            return self.backend_client.finance_bank_accounts_delete(
                profile_id=request.profile_id,
                bank_account_id=request.bank_account_id,
            )

        if tool_name == "finance_bank_accounts_can_delete":
            try:
                request = BankAccountDeleteRequest.model_validate(
                    {"profile_id": str(profile_id), "bank_account_id": payload.get("bank_account_id")}
                )
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Invalid payload for tool {tool_name}",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )
            return self.backend_client.finance_bank_accounts_can_delete(
                profile_id=request.profile_id,
                bank_account_id=request.bank_account_id,
            )

        if tool_name == "finance_bank_accounts_set_default":
            try:
                request_payload = _BankAccountSetDefaultByNamePayload.model_validate(payload)
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Invalid payload for tool {tool_name}",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )
            bank_account_id = request_payload.bank_account_id
            if bank_account_id is None:
                matched = self._find_bank_account_by_name(profile_id=profile_id, name=request_payload.name or "")
                if isinstance(matched, ToolError):
                    return matched
                bank_account_id = matched.id
            try:
                request = BankAccountSetDefaultRequest.model_validate(
                    {"profile_id": str(profile_id), "bank_account_id": str(bank_account_id)}
                )
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Invalid payload for tool {tool_name}",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )
            return self.backend_client.finance_bank_accounts_set_default(
                profile_id=request.profile_id,
                bank_account_id=request.bank_account_id,
            )

        if tool_name == "finance_merchants_rename":
            try:
                request = _MerchantRenamePayload.model_validate(payload)
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Invalid payload for tool {tool_name}",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )
            return self.backend_client.finance_merchants_rename(
                profile_id=profile_id,
                merchant_id=request.merchant_id,
                name=request.name,
            )

        if tool_name == "finance_merchants_merge":
            try:
                request = _MerchantsMergePayload.model_validate(payload)
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Invalid payload for tool {tool_name}",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )
            return self.backend_client.finance_merchants_merge(
                profile_id=profile_id,
                source_merchant_id=request.source_merchant_id,
                target_merchant_id=request.target_merchant_id,
            )


        if tool_name == "finance_merchants_suggest_fixes":
            try:
                request = _MerchantsSuggestFixesPayload.model_validate(payload)
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Invalid payload for tool {tool_name}",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )
            return self.backend_client.finance_merchants_suggest_fixes(
                profile_id=profile_id,
                status=request.status,
                limit=request.limit,
            )

        if tool_name == "finance_merchants_apply_suggestion":
            try:
                request = _MerchantsApplySuggestionPayload.model_validate(payload)
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Invalid payload for tool {tool_name}",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )
            return self.backend_client.finance_merchants_apply_suggestion(
                profile_id=profile_id,
                suggestion_id=request.suggestion_id,
            )

        return ToolError(
            code=ToolErrorCode.UNKNOWN_TOOL,
            message=f"Unknown tool: {tool_name}",
        )
