"""Deterministic auto-share suggestion service."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from backend.repositories.profiles_repository import ProfilesRepository
from backend.repositories.releves_repository import RelevesRepository
from backend.repositories.shared_expenses_repository import SharedExpensesRepository
from shared.models import DateRange, RelevesDirection, RelevesFilters


def apply_auto_share_suggestions_for_period(
    *,
    profile_id: UUID,
    start_date: date,
    end_date: date,
    releves_repository: RelevesRepository,
    profiles_repository: ProfilesRepository,
    shared_expenses_repository: SharedExpensesRepository,
) -> dict[str, int]:
    """Create deterministic pending share suggestions from category-level auto-share settings."""

    categories = profiles_repository.list_profile_categories(profile_id=profile_id)
    auto_share_categories = [
        category
        for category in categories
        if bool(category.get("auto_share_enabled"))
        and category.get("auto_share_to_profile_id") is not None
        and category.get("auto_share_link_id") is not None
        and category.get("id") is not None
    ]
    if not auto_share_categories:
        return {"candidates_count": 0, "created_suggestions_count": 0}

    suggestions: list[dict[str, object]] = []
    candidates_count = 0

    for category in auto_share_categories:
        category_id = UUID(str(category["id"]))
        offset = 0
        while True:
            rows, _ = releves_repository.list_releves(
                RelevesFilters(
                    profile_id=profile_id,
                    date_range=DateRange(start_date=start_date, end_date=end_date),
                    direction=RelevesDirection.DEBIT_ONLY,
                    include_internal_transfers=False,
                    category_id=category_id,
                    limit=500,
                    offset=offset,
                )
            )
            if not rows:
                break

            for releve in rows:
                candidates_count += 1
                split_ratio_other = Decimal(str(category.get("auto_share_split_ratio_other") or "0.5"))
                suggestions.append(
                    {
                        "transaction_id": releve.id,
                        "link_id": category.get("auto_share_link_id"),
                        "link_pair_id": None,
                        "suggested_to_profile_id": category.get("auto_share_to_profile_id"),
                        "suggested_split_ratio_other": split_ratio_other,
                        "confidence": 1.0,
                        "rationale": f"Auto-share catégorie: {category.get('name') or 'Sans nom'}",
                        "status": "pending",
                    }
                )

            if len(rows) < 500:
                break
            offset += 500

    created_suggestions_count = shared_expenses_repository.create_shared_expense_suggestions_bulk(
        profile_id=profile_id,
        suggestions=suggestions,
    )
    return {
        "candidates_count": candidates_count,
        "created_suggestions_count": created_suggestions_count,
    }
