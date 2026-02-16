"""Repository adapters for profils lookup."""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from backend.db.supabase_client import SupabaseClient


class ProfilesRepository(Protocol):
    def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None) -> UUID | None:
        """Return profile UUID for an authenticated user."""

    def get_chat_state(self, *, profile_id: UUID) -> dict[str, Any]:
        """Return persisted chat state for a profile."""

    def update_chat_state(self, *, profile_id: UUID, chat_state: dict[str, Any]) -> None:
        """Persist chat state for a profile."""


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

    def get_chat_state(self, *, profile_id: UUID) -> dict[str, Any]:
        rows, _ = self._client.get_rows(
            table="profils",
            query={"select": "chat_state", "id": f"eq.{profile_id}", "limit": 1},
            with_count=False,
            use_anon_key=False,
        )
        if not rows:
            return {}
        chat_state = rows[0].get("chat_state")
        if isinstance(chat_state, dict):
            return chat_state
        return {}

    def update_chat_state(self, *, profile_id: UUID, chat_state: dict[str, Any]) -> None:
        self._client.patch_rows(
            table="profils",
            query={"id": f"eq.{profile_id}"},
            payload={"chat_state": chat_state},
            use_anon_key=False,
        )
