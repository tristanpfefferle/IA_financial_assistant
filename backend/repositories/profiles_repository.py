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
        conversation_id = str(profile_id)
        rows, _ = self._client.get_rows(
            table="chat_state",
            query={
                "select": "active_task,state,last_filters,agent_state,active_filters,last_intent,last_metric,last_result_summary,tone",
                "conversation_id": f"eq.{conversation_id}",
                "limit": 1,
            },
            with_count=False,
            use_anon_key=False,
        )
        if not rows:
            return {}
        row = rows[0]
        return {key: value for key, value in row.items() if value is not None}

    def update_chat_state(self, *, profile_id: UUID, chat_state: dict[str, Any]) -> None:
        conversation_id = str(profile_id)
        payload = {
            "conversation_id": conversation_id,
            "profile_id": str(profile_id),
            "active_task": chat_state.get("active_task"),
            "state": chat_state.get("state"),
        }

        rows, _ = self._client.get_rows(
            table="chat_state",
            query={"select": "conversation_id", "conversation_id": f"eq.{conversation_id}", "limit": 1},
            with_count=False,
            use_anon_key=False,
        )
        if rows:
            self._client.patch_rows(
                table="chat_state",
                query={"conversation_id": f"eq.{conversation_id}"},
                payload=payload,
                use_anon_key=False,
            )
            return

        self._client.post_rows(
            table="chat_state",
            payload=payload,
            use_anon_key=False,
            prefer="resolution=merge-duplicates,return=representation",
        )
