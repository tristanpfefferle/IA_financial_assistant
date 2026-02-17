"""Tests for Supabase bank-accounts repository payload shaping."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from backend.repositories.bank_accounts_repository import SupabaseBankAccountsRepository
from shared.models import BankAccountCreateRequest, BankAccountUpdateRequest

PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
BANK_ACCOUNT_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _repository() -> SupabaseBankAccountsRepository:
    fake_client = SimpleNamespace(settings=SimpleNamespace(service_role_key="service", url="https://example.test"))
    return SupabaseBankAccountsRepository(client=fake_client)  # type: ignore[arg-type]


def test_create_bank_account_omits_null_kind_fields() -> None:
    repository = _repository()
    captured: dict[str, object] = {}

    def _fake_request_rows(*, table: str, method: str, query: object, body: dict[str, object] | None = None) -> list[dict[str, object]]:
        captured["table"] = table
        captured["method"] = method
        captured["body"] = body or {}
        return [
            {
                "id": str(BANK_ACCOUNT_ID),
                "profile_id": str(PROFILE_ID),
                "name": "Compte courant",
                "kind": "individual",
                "account_kind": "personal_current",
                "is_system": False,
            }
        ]

    repository._request_rows = _fake_request_rows  # type: ignore[method-assign]

    repository.create_bank_account(BankAccountCreateRequest(profile_id=PROFILE_ID, name="Compte courant"))

    assert captured["table"] == "bank_accounts"
    assert captured["method"] == "POST"
    assert captured["body"] == {
        "profile_id": str(PROFILE_ID),
        "name": "Compte courant",
    }


def test_update_bank_account_omits_fields_with_none_values() -> None:
    repository = _repository()
    captured: dict[str, object] = {}

    def _fake_request_rows(*, table: str, method: str, query: object, body: dict[str, object] | None = None) -> list[dict[str, object]]:
        captured["body"] = body or {}
        return [
            {
                "id": str(BANK_ACCOUNT_ID),
                "profile_id": str(PROFILE_ID),
                "name": "Compte principal",
                "kind": "individual",
                "account_kind": "personal_current",
                "is_system": False,
            }
        ]

    repository._request_rows = _fake_request_rows  # type: ignore[method-assign]

    update_request = BankAccountUpdateRequest.model_construct(
        profile_id=PROFILE_ID,
        bank_account_id=BANK_ACCOUNT_ID,
        set={"name": "Compte principal", "kind": None},
    )
    repository.update_bank_account(update_request)

    assert captured["body"] == {"name": "Compte principal"}
