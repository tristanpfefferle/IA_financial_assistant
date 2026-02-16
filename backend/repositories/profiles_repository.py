"""Repository adapters for profils lookup."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from backend.db.supabase_client import SupabaseClient


class ProfilesRepository(Protocol):
    def get_profile_id_by_email(self, email: str) -> UUID | None:
        """Return profile UUID for an email when found."""


class SupabaseProfilesRepository:
    """Supabase repository for profils table lookups."""

    def __init__(self, client: SupabaseClient) -> None:
        self._client = client

    def get_profile_id_by_email(self, email: str) -> UUID | None:
        rows, _ = self._client.get_rows(
            table="profils",
            query={"select": "id", "email": f"eq.{email}", "limit": 1},
            with_count=False,
            use_anon_key=False,
        )
        if not rows:
            return None
        profile_id = rows[0].get("id")
        if not profile_id:
            return None
        return UUID(str(profile_id))
