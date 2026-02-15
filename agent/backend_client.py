"""Backend client abstraction for agent (in-process by default)."""

from __future__ import annotations

from dataclasses import dataclass

from backend.services.tools import BackendToolService
from shared.models import ToolError, Transaction, TransactionFilters


@dataclass(slots=True)
class BackendClient:
    tool_service: BackendToolService

    def search_transactions(self, filters: TransactionFilters) -> list[Transaction] | ToolError:
        return self.tool_service.search_transactions(filters)
