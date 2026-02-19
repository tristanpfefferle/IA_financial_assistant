"""Repository adapters for profils lookup."""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol
from uuid import UUID

from backend.db.supabase_client import SupabaseClient
from shared.models import PROFILE_DEFAULT_CORE_FIELDS


class ProfilesRepository(Protocol):
    def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None) -> UUID | None:
        """Return profile UUID for an authenticated user."""

    def get_chat_state(self, *, profile_id: UUID, user_id: UUID) -> dict[str, Any]:
        """Return persisted chat state for a profile."""

    def update_chat_state(self, *, profile_id: UUID, user_id: UUID, chat_state: dict[str, Any]) -> None:
        """Persist chat state for a profile."""

    def get_profile_fields(self, *, profile_id: UUID, fields: list[str] | None = None) -> dict[str, Any]:
        """Return selected profile columns for one profile id."""

    def update_profile_fields(self, *, profile_id: UUID, set_dict: dict[str, Any]) -> dict[str, Any]:
        """Update selected profile columns for one profile id and return updated values."""

    def list_bank_accounts(self, *, profile_id: UUID) -> list[dict[str, Any]]:
        """Return bank accounts linked to a profile."""

    def ensure_bank_accounts(self, *, profile_id: UUID, names: list[str]) -> dict[str, Any]:
        """Create missing bank accounts while preserving uniqueness by lowercase name."""



class SupabaseProfilesRepository:
    """Supabase repository for profils table lookups."""

    def __init__(self, client: SupabaseClient) -> None:
        self._client = client

    def _get_profile_id_by_column(self, *, column: str, value: str) -> UUID | None:
        rows, _ = self._client.get_rows(
            table="profils",
            query={"select": "id", column: f"eq.{value}", "limit": 1},
            with_count=False,
            use_anon_key=False,
        )
        if not rows:
            return None
        profile_id = rows[0].get("id")
        if not profile_id:
            return None
        return UUID(str(profile_id))

    def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None) -> UUID | None:
        profile_id = self._get_profile_id_by_column(column="account_id", value=str(auth_user_id))
        if profile_id is not None:
            return profile_id

        if email:
            return self._get_profile_id_by_column(column="email", value=email)
        return None

    def get_chat_state(self, *, profile_id: UUID, user_id: UUID) -> dict[str, Any]:
        conversation_id = str(profile_id)
        rows, _ = self._client.get_rows(
            table="chat_state",
            query={
                "select": "active_task,state",
                "conversation_id": f"eq.{conversation_id}",
                "profile_id": f"eq.{profile_id}",
                "user_id": f"eq.{user_id}",
                "limit": 1,
            },
            with_count=False,
            use_anon_key=False,
        )
        if not rows:
            return {}
        row = rows[0] or {}
        result: dict[str, Any] = {}
        active_task = row.get("active_task")
        state = row.get("state")
        if active_task is not None:
            result["active_task"] = active_task
        if state is not None:
            result["state"] = state
        return result

    def update_chat_state(self, *, profile_id: UUID, user_id: UUID, chat_state: dict[str, Any]) -> None:
        conversation_id = str(profile_id)
        payload = {
            "conversation_id": conversation_id,
            "user_id": str(user_id),
            "profile_id": str(profile_id),
        }

        if "active_task" in chat_state:
            payload["active_task"] = chat_state.get("active_task")
        if "state" in chat_state:
            payload["state"] = chat_state.get("state")

        self._client.upsert_row(
            table="chat_state",
            payload=payload,
            on_conflict="conversation_id",
            use_anon_key=False,
        )

    @staticmethod
    def _serialize_profile_value(value: Any) -> Any:
        if isinstance(value, (date, UUID)):
            return str(value)
        return value

    @staticmethod
    def _filter_allowed_profile_updates(set_dict: dict[str, Any]) -> dict[str, Any]:
        allowed_fields = {"first_name", "last_name", "birth_date"}
        return {
            field: SupabaseProfilesRepository._serialize_profile_value(value)
            for field, value in set_dict.items()
            if field in allowed_fields
        }

    def get_profile_fields(self, *, profile_id: UUID, fields: list[str] | None = None) -> dict[str, Any]:
        selected_fields = list(fields or PROFILE_DEFAULT_CORE_FIELDS)
        select_clause = ",".join(dict.fromkeys(selected_fields))
        rows, _ = self._client.get_rows(
            table="profils",
            query={"select": select_clause, "id": f"eq.{profile_id}", "limit": 1},
            with_count=False,
            use_anon_key=False,
        )
        if not rows:
            raise ValueError("Profile not found")

        row = rows[0]
        return {field: row.get(field) for field in selected_fields}

    def update_profile_fields(self, *, profile_id: UUID, set_dict: dict[str, Any]) -> dict[str, Any]:
        filtered_set_dict = self._filter_allowed_profile_updates(set_dict)
        if not filtered_set_dict:
            return {}

        if hasattr(self._client, "table"):
            response = (
                self._client.table("profils")
                .update(filtered_set_dict)
                .eq("id", str(profile_id))
                .execute()
            )
            response_data = getattr(response, "data", None)
            if response_data == []:
                raise ValueError("Profile not found")
            return dict(filtered_set_dict)

        rows = self._client.patch_rows(
            table="profils",
            query={"id": f"eq.{profile_id}"},
            payload=filtered_set_dict,
            use_anon_key=False,
        )
        if not rows:
            raise ValueError("Profile not found")

        row = rows[0]
        return {field: row.get(field) for field in filtered_set_dict}


    @staticmethod
    def _normalize_bank_account_names(names: list[str]) -> list[str]:
        normalized_names: list[str] = []
        seen_lower: set[str] = set()
        for raw_name in names:
            cleaned_name = " ".join(str(raw_name).strip().split())
            if not cleaned_name:
                continue
            lowered_name = cleaned_name.lower()
            if lowered_name in seen_lower:
                continue
            seen_lower.add(lowered_name)
            normalized_names.append(cleaned_name)
        return normalized_names

    def list_bank_accounts(self, *, profile_id: UUID) -> list[dict[str, Any]]:
        rows, _ = self._client.get_rows(
            table="bank_accounts",
            query={
                "select": "id,name,account_kind,kind",
                "profile_id": f"eq.{profile_id}",
                "limit": 200,
            },
            with_count=False,
            use_anon_key=False,
        )
        return rows

    def ensure_bank_accounts(self, *, profile_id: UUID, names: list[str]) -> dict[str, Any]:
        normalized_names = self._normalize_bank_account_names(names)
        existing_rows = self.list_bank_accounts(profile_id=profile_id)
        existing_by_lower = {str(row.get("name", "")).strip().lower(): row for row in existing_rows if row.get("name")}

        created: list[str] = []
        existing: list[str] = []

        for name in normalized_names:
            lowered_name = name.lower()
            if lowered_name in existing_by_lower:
                existing.append(name)
                continue

            payload = {
                "profile_id": str(profile_id),
                "name": name,
                "kind": "individual",
                "account_kind": "personal_current",
                "is_system": False,
            }
            try:
                self._client.post_rows(table="bank_accounts", payload=payload, use_anon_key=False)
            except RuntimeError as exc:
                error_message = str(exc).lower()
                if "duplicate key" in error_message or "unique" in error_message:
                    existing.append(name)
                    continue
                raise

            created.append(name)
            existing_by_lower[lowered_name] = {"name": name}

        return {"created": created, "existing": existing, "all": normalized_names}
