"""Composition root for backend services."""

from __future__ import annotations

from backend.db.supabase_client import SupabaseClient, SupabaseSettings
from backend.repositories.releves_repository import InMemoryRelevesRepository, SupabaseRelevesRepository
from backend.repositories.transactions_repository import GestionFinanciereTransactionsRepository
from backend.services.tools import BackendToolService
from shared import config


def build_backend_tool_service() -> BackendToolService:
    """Build backend tool service with repository adapters.

    The default repository is an in-process placeholder adapter over the
    `gestion_financiere` source-of-truth code.
    """

    transactions_repository = GestionFinanciereTransactionsRepository()

    supabase_url = config.supabase_url()
    supabase_key = config.supabase_service_role_key()
    if supabase_url and supabase_key:
        supabase_client = SupabaseClient(
            settings=SupabaseSettings(
                url=supabase_url,
                service_role_key=supabase_key,
                anon_key=config.supabase_anon_key(),
            )
        )
        releves_repository = SupabaseRelevesRepository(client=supabase_client)
    else:
        releves_repository = InMemoryRelevesRepository()

    return BackendToolService(
        transactions_repository=transactions_repository,
        releves_repository=releves_repository,
    )
