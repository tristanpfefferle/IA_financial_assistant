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
        serialized_set_dict = {
            field: self._serialize_profile_value(value) for field, value in set_dict.items()
        }
        rows = self._client.patch_rows(
            table="profils",
            query={"id": f"eq.{profile_id}"},
            payload=serialized_set_dict,
            use_anon_key=False,
        )
        if not rows:
            raise ValueError("Profile not found")

        row = rows[0]
        return {field: row.get(field) for field in set_dict}
