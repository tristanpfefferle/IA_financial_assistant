"""Batch job stub to enrich merchant_entities.suggested_category_norm."""

from __future__ import annotations

from backend.db.supabase_client import SupabaseClient
from backend.repositories.profiles_repository import SupabaseProfilesRepository
from uuid import UUID

from shared.config import load_settings


def _suggest_category(canonical_name_norm: str) -> str | None:
    name = canonical_name_norm.lower()
    if "coop" in name or "migros" in name:
        return "alimentation"
    if any(token in name for token in ("shell", "bp", "avia", "esso")):
        return "transport"
    return None


def run(*, limit: int = 200) -> int:
    settings = load_settings()
    client = SupabaseClient(settings.supabase)
    repository = SupabaseProfilesRepository(client=client)

    rows = repository.list_merchant_entities_missing_suggested_category(limit=limit)
    updated = 0
    for row in rows:
        merchant_entity_id = row.get("id")
        canonical_name_norm = str(row.get("canonical_name_norm") or "").strip()
        if not merchant_entity_id or not canonical_name_norm:
            continue

        suggested = _suggest_category(canonical_name_norm)
        if suggested is None:
            continue

        repository.update_merchant_entity_suggested_category_norm(
            merchant_entity_id=UUID(str(merchant_entity_id)),
            suggested_category_norm=suggested,
        )
        updated += 1

    print(f"enrich_merchants_suggested_category: updated={updated} scanned={len(rows)}")
    return updated


if __name__ == "__main__":
    run()
