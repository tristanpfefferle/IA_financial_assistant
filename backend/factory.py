"""Composition root for backend services."""

from __future__ import annotations

from backend.repositories.transactions_repository import GestionFinanciereTransactionsRepository
from backend.services.tools import BackendToolService


def build_backend_tool_service() -> BackendToolService:
    """Build backend tool service with repository adapters.

    The default repository is an in-process placeholder adapter over the
    `gestion_financiere` source-of-truth code.
    """

    transactions_repository = GestionFinanciereTransactionsRepository()
    return BackendToolService(transactions_repository=transactions_repository)
