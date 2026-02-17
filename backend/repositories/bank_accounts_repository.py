"""Repository interfaces and adapters for bank accounts CRUD."""

from __future__ import annotations

import json
from typing import Any, Protocol
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

from backend.db.supabase_client import SupabaseClient
from shared.models import (
    BankAccount,
    BankAccountCreateRequest,
    BankAccountDeleteRequest,
    BankAccountSetDefaultRequest,
    BankAccountUpdateRequest,
)


class BankAccountsRepository(Protocol):
    def list_bank_accounts(self, profile_id: UUID) -> list[BankAccount]:
        """Return bank accounts for one profile."""

    def create_bank_account(self, request: BankAccountCreateRequest) -> BankAccount:
        """Create one bank account for one profile."""

    def update_bank_account(self, request: BankAccountUpdateRequest) -> BankAccount:
        """Update one bank account for one profile."""

    def delete_bank_account(self, request: BankAccountDeleteRequest) -> None:
        """Delete one bank account for one profile."""

    def set_default_bank_account(self, request: BankAccountSetDefaultRequest) -> UUID:
        """Set default profile bank account and return selected id."""


class InMemoryBankAccountsRepository:
    """In-memory bank accounts repository used by tests/dev."""

    def __init__(self) -> None:
        self._accounts: list[BankAccount] = []
        self._default_by_profile: dict[UUID, UUID] = {}

    def list_bank_accounts(self, profile_id: UUID) -> list[BankAccount]:
        return [item for item in self._accounts if item.profile_id == profile_id]

    def create_bank_account(self, request: BankAccountCreateRequest) -> BankAccount:
        normalized_name = request.name.strip().lower()
        for account in self._accounts:
            if account.profile_id != request.profile_id:
                continue
            if account.name.strip().lower() == normalized_name:
                raise ValueError("bank account name already exists")

        account = BankAccount(
            id=uuid4(),
            profile_id=request.profile_id,
            name=request.name,
            kind=request.kind,
            account_kind=request.account_kind,
            is_system=False,
        )
        self._accounts.append(account)
        return account

    def update_bank_account(self, request: BankAccountUpdateRequest) -> BankAccount:
        for index, account in enumerate(self._accounts):
            if (
                account.profile_id != request.profile_id
                or account.id != request.bank_account_id
            ):
                continue
            updated = account.model_copy(update=request.set)
            self._accounts[index] = updated
            return updated
        raise ValueError("Bank account not found")

    def delete_bank_account(self, request: BankAccountDeleteRequest) -> None:
        kept = [
            account
            for account in self._accounts
            if not (
                account.profile_id == request.profile_id
                and account.id == request.bank_account_id
            )
        ]
        if len(kept) == len(self._accounts):
            raise ValueError("Bank account not found")
        self._accounts = kept
        if self._default_by_profile.get(request.profile_id) == request.bank_account_id:
            self._default_by_profile.pop(request.profile_id, None)

    def set_default_bank_account(self, request: BankAccountSetDefaultRequest) -> UUID:
        exists = any(
            account.profile_id == request.profile_id
            and account.id == request.bank_account_id
            for account in self._accounts
        )
        if not exists:
            raise ValueError("Bank account not found")
        self._default_by_profile[request.profile_id] = request.bank_account_id
        return request.bank_account_id


class SupabaseBankAccountsRepository:
    """Supabase-backed bank accounts repository."""

    def __init__(self, client: SupabaseClient) -> None:
        self._client = client

    def _request_rows(
        self,
        *,
        table: str,
        method: str,
        query: list[tuple[str, str | int]] | dict[str, str | int],
        body: dict[str, object] | None = None,
    ) -> list[dict[str, Any]]:
        encoded_query = urlencode(query, doseq=True)
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        api_key = self._client.settings.service_role_key
        request = Request(
            url=f"{self._client.settings.url}/rest/v1/{table}?{encoded_query}",
            data=payload,
            headers={
                "apikey": api_key,
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
            method=method,
        )

        try:
            with urlopen(request) as response:  # noqa: S310 - URL comes from trusted env config
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(
                f"Supabase request failed with status {exc.code}: {body_text}"
            ) from exc

    def list_bank_accounts(self, profile_id: UUID) -> list[BankAccount]:
        rows, _ = self._client.get_rows(
            table="bank_accounts",
            query=[
                ("profile_id", f"eq.{profile_id}"),
                ("select", "id,profile_id,name,kind,account_kind,is_system"),
                ("order", "created_at.asc"),
            ],
            with_count=False,
            use_anon_key=False,
        )
        return [BankAccount.model_validate(row) for row in rows]

    def create_bank_account(self, request: BankAccountCreateRequest) -> BankAccount:
        normalized_name = request.name.strip()
        existing_rows, _ = self._client.get_rows(
            table="bank_accounts",
            query={
                "select": "id",
                "profile_id": f"eq.{request.profile_id}",
                "name": f"ilike.{normalized_name}",
                "limit": 1,
            },
            with_count=False,
            use_anon_key=False,
        )
        if existing_rows:
            raise ValueError("bank account name already exists")

        payload: dict[str, object] = {
            "profile_id": str(request.profile_id),
            "name": request.name,
        }
        if request.kind is not None:
            payload["kind"] = request.kind
        if request.account_kind is not None:
            payload["account_kind"] = request.account_kind

        rows = self._request_rows(
            table="bank_accounts",
            method="POST",
            query={"select": "id,profile_id,name,kind,account_kind,is_system"},
            body=payload,
        )
        if not rows:
            raise RuntimeError("Supabase did not return created bank account")
        return BankAccount.model_validate(rows[0])

    def update_bank_account(self, request: BankAccountUpdateRequest) -> BankAccount:
        payload = {
            field_name: value
            for field_name, value in request.set.items()
            if value is not None
        }
        rows = self._request_rows(
            table="bank_accounts",
            method="PATCH",
            query={
                "id": f"eq.{request.bank_account_id}",
                "profile_id": f"eq.{request.profile_id}",
                "select": "id,profile_id,name,kind,account_kind,is_system",
            },
            body=payload,
        )
        if not rows:
            raise ValueError("Bank account not found")
        return BankAccount.model_validate(rows[0])

    def _has_related_transactions(self, request: BankAccountDeleteRequest) -> bool:
        try:
            rows, _ = self._client.get_rows(
                table="releves_bancaires",
                query={
                    "select": "id",
                    "profile_id": f"eq.{request.profile_id}",
                    "bank_account_id": f"eq.{request.bank_account_id}",
                    "limit": 1,
                },
                with_count=False,
                use_anon_key=False,
            )
            return bool(rows)
        except RuntimeError as exc:
            message = str(exc)
            if "42P01" in message or "does not exist" in message:
                return False
            raise

    def delete_bank_account(self, request: BankAccountDeleteRequest) -> None:
        if self._has_related_transactions(request):
            raise ValueError("bank account not empty")

        try:
            self._client.patch_rows(
                table="profils",
                query={
                    "id": f"eq.{request.profile_id}",
                    "default_bank_account_id": f"eq.{request.bank_account_id}",
                },
                payload={"default_bank_account_id": None},
                use_anon_key=False,
            )
        except RuntimeError:
            pass

        rows = self._request_rows(
            table="bank_accounts",
            method="DELETE",
            query={
                "id": f"eq.{request.bank_account_id}",
                "profile_id": f"eq.{request.profile_id}",
                "select": "id",
            },
            body=None,
        )
        if not rows:
            raise ValueError("Bank account not found")

    def set_default_bank_account(self, request: BankAccountSetDefaultRequest) -> UUID:
        account_rows, _ = self._client.get_rows(
            table="bank_accounts",
            query={
                "select": "id",
                "id": f"eq.{request.bank_account_id}",
                "profile_id": f"eq.{request.profile_id}",
                "limit": 1,
            },
            with_count=False,
            use_anon_key=False,
        )
        if not account_rows:
            raise ValueError("Bank account not found")

        rows = self._client.patch_rows(
            table="profils",
            query={"id": f"eq.{request.profile_id}"},
            payload={"default_bank_account_id": str(request.bank_account_id)},
            use_anon_key=False,
        )
        if not rows:
            raise ValueError("Profile not found")

        return request.bank_account_id
