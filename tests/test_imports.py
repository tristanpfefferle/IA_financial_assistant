"""Minimal architecture import tests."""


def test_import_shared_models() -> None:
    from shared.models import Account, Category, DateRange, Money, ToolError, Transaction, TransactionFilters

    assert all([Money, DateRange, Transaction, Account, Category, TransactionFilters, ToolError])


def test_import_backend_modules() -> None:
    from backend.db.supabase_client import SupabaseClient, SupabaseSettings
    from backend.services.tools import BackendToolService

    assert SupabaseClient and SupabaseSettings and BackendToolService


def test_import_agent_modules() -> None:
    from agent.backend_client import BackendClient
    from agent.loop import AgentLoop
    from agent.tool_router import ToolRouter

    assert BackendClient and ToolRouter and AgentLoop
