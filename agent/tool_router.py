"""Minimal tool router for mapping tool names to backend client methods."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import ValidationError

from agent.backend_client import BackendClient
from shared.models import RelevesFilters, RelevesSearchResult, RelevesSumResult, ToolError, ToolErrorCode


@dataclass(slots=True)
class ToolRouter:
    backend_client: BackendClient

    def call(
        self,
        tool_name: str,
        payload: dict,
        *,
        profile_id: UUID | None = None,
    ) -> RelevesSearchResult | RelevesSumResult | ToolError:
        if tool_name in {"finance_transactions_search", "finance_releves_search"}:
            # finance_transactions_search is deprecated: alias to finance_releves_search.
            if profile_id is None:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Missing profile_id context for tool {tool_name}",
                )
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
            # finance_transactions_sum is deprecated: alias to finance_releves_sum.
            if profile_id is None:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Missing profile_id context for tool {tool_name}",
                )
            try:
                filters = RelevesFilters.model_validate({**payload, "profile_id": str(profile_id)})
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message=f"Invalid payload for tool {tool_name}",
                    details={"validation_errors": exc.errors(), "payload": payload},
                )
            return self.backend_client.releves_sum(filters)

        return ToolError(
            code=ToolErrorCode.UNKNOWN_TOOL,
            message=f"Unknown tool: {tool_name}",
        )
