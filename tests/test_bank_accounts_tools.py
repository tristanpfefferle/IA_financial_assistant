"""Unit tests for finance_bank_accounts_* backend tool behavior."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from backend.services.tools import BackendToolService
from shared.models import BankAccount, BankAccountsListResult, ToolError, ToolErrorCode

PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
BANK_ACCOUNT_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _service(*, profile_fields: dict[str, object] | Exception) -> BackendToolService:
    bank_accounts_repository = SimpleNamespace(
        list_bank_accounts=lambda *, profile_id: [
            BankAccount(
                id=BANK_ACCOUNT_ID,
                profile_id=profile_id,
                name="UBS",
                kind="individual",
                account_kind="personal_current",
                is_system=False,
            )
        ]
    )

    def _get_profile_fields(
        *, profile_id: UUID, fields: list[str] | None = None
    ) -> dict[str, object]:
        if isinstance(profile_fields, Exception):
            raise profile_fields
        return profile_fields

    profiles_repository = SimpleNamespace(get_profile_fields=_get_profile_fields)

    return BackendToolService(
        transactions_repository=SimpleNamespace(),
        releves_repository=SimpleNamespace(),
        categories_repository=SimpleNamespace(),
        bank_accounts_repository=bank_accounts_repository,
        profiles_repository=profiles_repository,
    )


def test_finance_bank_accounts_list_returns_not_found_when_profile_is_missing() -> None:
    service = _service(profile_fields=ValueError("Profile not found"))

    result = service.finance_bank_accounts_list(profile_id=PROFILE_ID)

    assert isinstance(result, ToolError)
    assert result.code == ToolErrorCode.NOT_FOUND


def test_finance_bank_accounts_list_ignores_invalid_default_bank_account_id() -> None:
    service = _service(profile_fields={"default_bank_account_id": "not-a-uuid"})

    result = service.finance_bank_accounts_list(profile_id=PROFILE_ID)

    assert isinstance(result, BankAccountsListResult)
    assert result.default_bank_account_id is None


def test_finance_bank_accounts_create_returns_conflict_for_duplicate_name() -> None:
    bank_accounts_repository = SimpleNamespace(
        create_bank_account=lambda request: (_ for _ in ()).throw(ValueError("bank account name already exists"))
    )
    service = BackendToolService(
        transactions_repository=SimpleNamespace(),
        releves_repository=SimpleNamespace(),
        categories_repository=SimpleNamespace(),
        bank_accounts_repository=bank_accounts_repository,
        profiles_repository=SimpleNamespace(get_profile_fields=lambda **kwargs: {}),
    )

    result = service.finance_bank_accounts_create(profile_id=PROFILE_ID, name="UBS")

    assert isinstance(result, ToolError)
    assert result.code == ToolErrorCode.CONFLICT
    assert result.message == "bank account name already exists"


def test_finance_bank_accounts_can_delete_returns_not_empty_reason() -> None:
    bank_accounts_repository = SimpleNamespace(can_delete_bank_account=lambda request: False)
    service = BackendToolService(
        transactions_repository=SimpleNamespace(),
        releves_repository=SimpleNamespace(),
        categories_repository=SimpleNamespace(),
        bank_accounts_repository=bank_accounts_repository,
        profiles_repository=SimpleNamespace(get_profile_fields=lambda **kwargs: {}),
    )

    result = service.finance_bank_accounts_can_delete(
        profile_id=PROFILE_ID,
        bank_account_id=BANK_ACCOUNT_ID,
    )

    assert result == {"ok": True, "can_delete": False, "reason": "not_empty"}
