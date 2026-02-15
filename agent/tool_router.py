"""Minimal tool router for mapping tool names to backend client methods."""

from __future__ import annotations

from dataclasses import dataclass

from agent.backend_client import BackendClient
from shared.models import ToolError, TransactionFilters


@dataclass(slots=True)
class ToolRouter:
    backend_client: BackendClient

    def call(self, tool_name: str, payload: dict) -> object:
        if tool_name == "finance.transactions.search":
            filters = TransactionFilters.model_validate(payload)
            return self.backend_client.search_transactions(filters)
        return ToolError(code="UNKNOWN_TOOL", message=f"Unknown tool: {tool_name}")
