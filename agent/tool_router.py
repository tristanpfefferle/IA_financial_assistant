"""Minimal tool router for mapping tool names to backend client methods."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import ValidationError

from agent.backend_client import BackendClient
from shared.models import (
    RelevesFilters,
    RelevesSearchResult,
    RelevesSumResult,
    ToolError,
    ToolErrorCode,
    TransactionFilters,
    TransactionSearchResult,
    TransactionSumResult,
)


@dataclass(slots=True)
class ToolRouter:
    backend_client: BackendClient

    def call(
        self,
        tool_name: str,
        payload: dict,
        *,
        profile_id: UUID | None = None,
    ) -> TransactionSearchResult | TransactionSumResult | RelevesSearchResult | RelevesSumResult | ToolError:
        if tool_name == "finance_transactions_search":
            try:
                filters = TransactionFilters.model_validate(payload)
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message="Invalid payload for tool finance_transactions_search",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )
            return self.backend_client.search_transactions(filters)

        if tool_name == "finance_transactions_sum":
            try:
                filters = TransactionFilters.model_validate(payload)
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message="Invalid payload for tool finance_transactions_sum",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )
            return self.backend_client.sum_transactions(filters)

        if tool_name == "finance_releves_search":
            if profile_id is None:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message="Missing profile_id context for tool finance_releves_search",
                )
            try:
                filters = RelevesFilters.model_validate({**payload, "profile_id": str(profile_id)})
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message="Invalid payload for tool finance_releves_search",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )
            return self.backend_client.releves_search(filters)

        if tool_name == "finance_releves_sum":
            if profile_id is None:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message="Missing profile_id context for tool finance_releves_sum",
                )
            try:
                filters = RelevesFilters.model_validate({**payload, "profile_id": str(profile_id)})
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message="Invalid payload for tool finance_releves_sum",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )
            return self.backend_client.releves_sum(filters)

        return ToolError(
            code=ToolErrorCode.UNKNOWN_TOOL,
            message=f"Unknown tool: {tool_name}",
        )
