"""Composition root for backend services."""

from __future__ import annotations

from backend.db.supabase_client import SupabaseClient, SupabaseSettings
from backend.repositories.categories_repository import (
    InMemoryCategoriesRepository,
    SupabaseCategoriesRepository,
)
from backend.repositories.bank_accounts_repository import (
    InMemoryBankAccountsRepository,
    SupabaseBankAccountsRepository,
)
from backend.repositories.profiles_repository import SupabaseProfilesRepository
from backend.repositories.releves_repository import InMemoryRelevesRepository, SupabaseRelevesRepository
from backend.repositories.transactions_repository import (
    InMemoryTransactionsRepository,
    SupabaseTransactionsRepository,
)
from backend.services.tools import BackendToolService
from shared import config


def build_backend_tool_service() -> BackendToolService:
    """Build backend tool service with repository adapters."""

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
        transactions_repository = SupabaseTransactionsRepository(client=supabase_client)
        categories_repository = SupabaseCategoriesRepository(client=supabase_client)
        bank_accounts_repository = SupabaseBankAccountsRepository(client=supabase_client)
        profiles_repository = SupabaseProfilesRepository(client=supabase_client)
    else:
        releves_repository = InMemoryRelevesRepository()
        transactions_repository = InMemoryTransactionsRepository()
        categories_repository = InMemoryCategoriesRepository()
        bank_accounts_repository = InMemoryBankAccountsRepository()
        profiles_repository = None

    return BackendToolService(
        transactions_repository=transactions_repository,
        releves_repository=releves_repository,
        categories_repository=categories_repository,
        bank_accounts_repository=bank_accounts_repository,
        profiles_repository=profiles_repository,
    )
