"""Minimal tool router for mapping tool names to backend client methods."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from agent.backend_client import BackendClient
from shared.models import ToolError, ToolErrorCode, TransactionFilters


@dataclass(slots=True)
class ToolRouter:
    backend_client: BackendClient

    def call(self, tool_name: str, payload: dict) -> object:
        if tool_name == "finance.transactions.search":
            try:
                filters = TransactionFilters.model_validate(payload)
            except ValidationError as exc:
                return ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message="Invalid payload for tool finance.transactions.search",
                    details={"validation_error": str(exc)},
                )
            return self.backend_client.search_transactions(filters)
        return ToolError(
            code=ToolErrorCode.UNKNOWN_TOOL,
            message=f"Unknown tool: {tool_name}",
        )
