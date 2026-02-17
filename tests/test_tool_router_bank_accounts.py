"""Tests for bank-account tool routing and name resolution."""

from __future__ import annotations

from uuid import UUID

from agent.tool_router import ToolRouter
from shared.models import BankAccount, ToolError, ToolErrorCode
from tests.fakes import FakeBackendClient

PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def test_bank_account_delete_by_name_not_found() -> None:
    router = ToolRouter(backend_client=FakeBackendClient())
    created = router.call("finance_bank_accounts_create", {"name": "Compte principal"}, profile_id=PROFILE_ID)
    assert isinstance(created, BankAccount)

    result = router.call("finance_bank_accounts_delete", {"name": "compte prinicpal"}, profile_id=PROFILE_ID)

    assert isinstance(result, ToolError)
    assert result.code == ToolErrorCode.NOT_FOUND
    assert result.details is not None
    assert result.details.get("name") == "compte prinicpal"
    assert result.details.get("close_names") == ["Compte principal"]


def test_bank_account_update_by_name_ambiguous() -> None:
    backend = FakeBackendClient()
    router = ToolRouter(backend_client=backend)
    first = router.call("finance_bank_accounts_create", {"name": "Joint"}, profile_id=PROFILE_ID)
    second = router.call("finance_bank_accounts_create", {"name": "JOINT"}, profile_id=PROFILE_ID)
    assert isinstance(first, BankAccount)
    assert isinstance(second, BankAccount)

    result = router.call(
        "finance_bank_accounts_update",
        {"name": "joint", "set": {"name": "Joint foyer"}},
        profile_id=PROFILE_ID,
    )

    assert isinstance(result, ToolError)
    assert result.code == ToolErrorCode.AMBIGUOUS
    assert result.details is not None
    assert result.details.get("name") == "joint"
    assert result.details.get("candidates") == [
        {"id": str(first.id), "name": "Joint"},
        {"id": str(second.id), "name": "JOINT"},
    ]


def test_bank_account_update_by_name_ok() -> None:
    router = ToolRouter(backend_client=FakeBackendClient())
    created = router.call("finance_bank_accounts_create", {"name": "Courant"}, profile_id=PROFILE_ID)
    assert isinstance(created, BankAccount)

    result = router.call(
        "finance_bank_accounts_update",
        {"name": "courant", "set": {"name": "Compte courant"}},
        profile_id=PROFILE_ID,
    )

    assert isinstance(result, BankAccount)
    assert result.name == "Compte courant"


def test_bank_account_create_validation_payload() -> None:
    router = ToolRouter(backend_client=FakeBackendClient())

    result = router.call(
        "finance_bank_accounts_create",
        {"name": "   ", "kind": "bad"},
        profile_id=PROFILE_ID,
    )

    assert isinstance(result, ToolError)
    assert result.code == ToolErrorCode.VALIDATION_ERROR
