"""Tests for Supabase bank-accounts repository payload shaping."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from backend.repositories.bank_accounts_repository import SupabaseBankAccountsRepository
from shared.models import (
    BankAccountCreateRequest,
    BankAccountDeleteRequest,
    BankAccountUpdateRequest,
)

PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
BANK_ACCOUNT_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _repository() -> SupabaseBankAccountsRepository:
    fake_client = SimpleNamespace(
        settings=SimpleNamespace(service_role_key="service", url="https://example.test"),
        get_rows=lambda **kwargs: ([], None),
    )
    return SupabaseBankAccountsRepository(client=fake_client)  # type: ignore[arg-type]


def test_create_bank_account_omits_null_kind_fields() -> None:
    repository = _repository()
    captured: dict[str, object] = {}

    def _fake_request_rows(
        *, table: str, method: str, query: object, body: dict[str, object] | None = None
    ) -> list[dict[str, object]]:
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

    repository.create_bank_account(
        BankAccountCreateRequest(profile_id=PROFILE_ID, name="Compte courant")
    )

    assert captured["table"] == "bank_accounts"
    assert captured["method"] == "POST"
    assert captured["body"] == {
        "profile_id": str(PROFILE_ID),
        "name": "Compte courant",
    }


def test_update_bank_account_omits_fields_with_none_values() -> None:
    repository = _repository()
    captured: dict[str, object] = {}

    def _fake_request_rows(
        *, table: str, method: str, query: object, body: dict[str, object] | None = None
    ) -> list[dict[str, object]]:
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


def test_delete_bank_account_continues_when_default_clear_fails() -> None:
    repository = _repository()

    repository._has_related_transactions = lambda request: False  # type: ignore[method-assign]

    calls: list[str] = []

    def _failing_patch_rows(**kwargs: object) -> list[dict[str, object]]:
        calls.append("patch")
        raise RuntimeError("column default_bank_account_id does not exist")

    repository._client.patch_rows = _failing_patch_rows  # type: ignore[attr-defined]

    def _fake_request_rows(
        *, table: str, method: str, query: object, body: dict[str, object] | None = None
    ) -> list[dict[str, object]]:
        calls.append(f"{method}:{table}")
        return [{"id": str(BANK_ACCOUNT_ID)}]

    repository._request_rows = _fake_request_rows  # type: ignore[method-assign]

    repository.delete_bank_account(
        BankAccountDeleteRequest(profile_id=PROFILE_ID, bank_account_id=BANK_ACCOUNT_ID)
    )

    assert calls == ["patch", "DELETE:bank_accounts"]


def test_create_bank_account_raises_value_error_when_name_already_exists() -> None:
    repository = _repository()

    repository._client.get_rows = lambda **kwargs: ([{"id": str(BANK_ACCOUNT_ID)}], None)  # type: ignore[attr-defined]

    try:
        repository.create_bank_account(
            BankAccountCreateRequest(profile_id=PROFILE_ID, name="Compte courant")
        )
    except ValueError as exc:
        assert str(exc) == "bank account name already exists"
    else:
        raise AssertionError("Expected ValueError for duplicate bank account name")


def test_create_bank_account_escapes_wildcards_in_ilike_query() -> None:
    repository = _repository()
    captured_query: dict[str, object] = {}

    def _capture_get_rows(**kwargs: object):
        query = kwargs.get("query")
        if isinstance(query, dict):
            captured_query.update(query)
        return ([], None)

    repository._client.get_rows = _capture_get_rows  # type: ignore[attr-defined]
    repository._request_rows = lambda **kwargs: [  # type: ignore[method-assign]
        {
            "id": str(BANK_ACCOUNT_ID),
            "profile_id": str(PROFILE_ID),
            "name": "Compte 50%_\\",
            "kind": "individual",
            "account_kind": "personal_current",
            "is_system": False,
        }
    ]

    repository.create_bank_account(
        BankAccountCreateRequest(profile_id=PROFILE_ID, name="Compte 50%_\\")
    )

    assert captured_query.get("name") == r"ilike.Compte 50\%\_\\"
