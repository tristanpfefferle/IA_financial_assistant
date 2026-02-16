"""Repository adapters for profils lookup."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from backend.db.supabase_client import SupabaseClient


class ProfilesRepository(Protocol):
    def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None) -> UUID | None:
        """Return profile UUID for an authenticated user."""


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
