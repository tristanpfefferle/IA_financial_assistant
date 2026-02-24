"""Repository adapters for per-profile share scoring rules."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from backend.db.supabase_client import SupabaseClient


class ShareRulesRepository(Protocol):
    """Persistence protocol for deterministic share-rule overrides."""

    def list_share_rules(self, profile_id: UUID) -> list[dict[str, Any]]:
        """List share rules configured for one profile."""

    def upsert_share_rule(
        self,
        profile_id: UUID,
        rule_type: str,
        rule_key: str,
        action: str,
        boost_value: Decimal | None,
    ) -> None:
        """Create or replace one share rule."""


class SupabaseShareRulesRepository:
    """Supabase implementation for profile share rules."""

    def __init__(self, *, client: SupabaseClient) -> None:
        self._client = client

    def list_share_rules(self, profile_id: UUID) -> list[dict[str, Any]]:
        rows, _ = self._client.get_rows(
            table="profile_share_rules",
            query={
                "select": "id,profile_id,rule_type,rule_key,action,boost_value,created_at",
                "profile_id": f"eq.{profile_id}",
                "limit": 300,
            },
            with_count=False,
            use_anon_key=False,
        )
        return rows

    def upsert_share_rule(
        self,
        profile_id: UUID,
        rule_type: str,
        rule_key: str,
        action: str,
        boost_value: Decimal | None,
    ) -> None:
        self._client.upsert_row(
            table="profile_share_rules",
            on_conflict="profile_id,rule_type,rule_key",
            payload={
                "profile_id": str(profile_id),
                "rule_type": rule_type,
                "rule_key": rule_key,
                "action": action,
                "boost_value": str(boost_value) if boost_value is not None else None,
            },
            use_anon_key=False,
        )


class InMemoryShareRulesRepository:
    """In-memory share rules storage for tests."""

    def __init__(self) -> None:
        self._rules: dict[tuple[UUID, str, str], dict[str, Any]] = {}

    def list_share_rules(self, profile_id: UUID) -> list[dict[str, Any]]:
        return [
            dict(rule)
            for (owner_profile_id, _, _), rule in self._rules.items()
            if owner_profile_id == profile_id
        ]

    def upsert_share_rule(
        self,
        profile_id: UUID,
        rule_type: str,
        rule_key: str,
        action: str,
        boost_value: Decimal | None,
    ) -> None:
        key = (profile_id, rule_type, rule_key)
        self._rules[key] = {
            "profile_id": profile_id,
            "rule_type": rule_type,
            "rule_key": rule_key,
            "action": action,
            "boost_value": boost_value,
        }
