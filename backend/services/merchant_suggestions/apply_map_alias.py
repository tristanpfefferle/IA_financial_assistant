"""Application service for applying `map_alias` merchant suggestions."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from backend.repositories.profiles_repository import ProfilesRepository


DEFAULT_COUNTRY = "CH"


def apply_map_alias_suggestion(
    *,
    profile_id: UUID,
    suggestion_id: UUID,
    repositories: ProfilesRepository,
) -> dict[str, Any]:
    """Apply one pending/failed map_alias suggestion and update its status."""

    merchant_entity_id: UUID | None = None
    alias_norm: str | None = None

    try:
        suggestion = repositories.get_merchant_suggestion_by_id(
            profile_id=profile_id,
            suggestion_id=suggestion_id,
        )
        if suggestion is None:
            raise ValueError("merchant suggestion not found")

        if str(suggestion.get("action") or "").strip().lower() != "map_alias":
            raise ValueError("suggestion action must be map_alias")

        observed_alias = str(suggestion.get("observed_alias") or "").strip()
        alias_norm = str(suggestion.get("observed_alias_norm") or "").strip()
        if not observed_alias:
            raise ValueError("missing observed_alias")
        if not alias_norm:
            raise ValueError("missing observed_alias_norm")

        target_entity_id = suggestion.get("target_merchant_entity_id")
        if target_entity_id:
            merchant_entity_id = UUID(str(target_entity_id))
        else:
            suggested_entity_name = str(suggestion.get("suggested_entity_name") or "").strip()
            suggested_entity_name_norm = str(suggestion.get("suggested_entity_name_norm") or "").strip()
            canonical_name = suggested_entity_name or suggested_entity_name_norm
            canonical_name_norm = suggested_entity_name_norm or suggested_entity_name
            if not canonical_name or not canonical_name_norm:
                raise ValueError("missing target_merchant_entity_id and suggested entity name")

            entity = repositories.get_merchant_entity_by_canonical_name_norm(
                country=DEFAULT_COUNTRY,
                canonical_name_norm=canonical_name_norm,
            )
            if entity is not None and entity.get("id"):
                merchant_entity_id = UUID(str(entity["id"]))
            else:
                merchant_entity_id = repositories.create_merchant_entity(
                    canonical_name=canonical_name,
                    canonical_name_norm=canonical_name_norm,
                    country=DEFAULT_COUNTRY,
                    suggested_category_norm=suggestion.get("suggested_category_norm"),
                    suggested_category_label=suggestion.get("suggested_category_label"),
                )

        repositories.upsert_merchant_alias(
            merchant_entity_id=merchant_entity_id,
            alias=observed_alias,
            alias_norm=alias_norm,
            source="suggestion_apply",
        )
        repositories.update_merchant_suggestion_status(
            profile_id=profile_id,
            suggestion_id=suggestion_id,
            status="applied",
            error=None,
        )
        return {
            "status": "applied",
            "suggestion_id": str(suggestion_id),
            "merchant_entity_id": str(merchant_entity_id),
            "alias_norm": alias_norm,
            "error": None,
        }
    except Exception as exc:
        error_message = str(exc)
        try:
            repositories.update_merchant_suggestion_status(
                profile_id=profile_id,
                suggestion_id=suggestion_id,
                status="failed",
                error=error_message,
            )
        except Exception:
            pass

        return {
            "status": "failed",
            "suggestion_id": str(suggestion_id),
            "merchant_entity_id": str(merchant_entity_id) if merchant_entity_id else None,
            "alias_norm": alias_norm,
            "error": error_message,
        }
