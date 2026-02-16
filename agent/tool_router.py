"""Minimal tool router for mapping tool names to backend client methods."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import get_close_matches
from uuid import UUID

from backend.repositories.category_utils import normalize_category_name
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from agent.backend_client import BackendClient
from shared.models import (
    CategoriesListResult,
    CategoryCreateRequest,
    CategoryDeleteRequest,
    CategoryUpdateRequest,
    ProfileCategory,
    RelevesAggregateRequest,
    RelevesAggregateResult,
    RelevesFilters,
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
        | CategoriesListResult
        | ProfileCategory
        | dict[str, bool]
        | ToolError
    ):
        if tool_name in {
            "finance_transactions_search",
            "finance_releves_search",
            "finance_transactions_sum",
            "finance_releves_sum",
            "finance_releves_aggregate",
            "finance_categories_list",
            "finance_categories_create",
            "finance_categories_update",
            "finance_categories_delete",
        } and profile_id is None:
            return ToolError(
                code=ToolErrorCode.VALIDATION_ERROR,
                message=f"Missing profile_id context for tool {tool_name}",
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

        return ToolError(
            code=ToolErrorCode.UNKNOWN_TOOL,
            message=f"Unknown tool: {tool_name}",
        )
