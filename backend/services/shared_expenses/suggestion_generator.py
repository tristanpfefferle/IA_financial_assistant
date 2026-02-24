"""Deterministic seed generator for initial shared-expense suggestions."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from backend.db.supabase_client import SupabaseClient
from backend.repositories.shared_expenses_repository import SharedExpensesRepository

_INTERNAL_TRANSFER_PATTERNS = ("virement", "transfer", "twint p2p")
_HOUSEHOLD_KEYWORDS = (
    "regie",
    "régie",
    "rent",
    "loyer",
    "assurance",
    "swisscom",
    "salt",
    "sunrise",
    "coop",
    "migros",
    "sbb",
    "tpg",
    "tl",
)
_HOUSEHOLD_CATEGORIES = {"logement", "alimentation", "assurance", "abonnements", "transport"}


def generate_initial_shared_expense_suggestions(
    *,
    profile_id: UUID,
    household_link: dict[str, Any],
    shared_expenses_repository: SharedExpensesRepository,
    supabase_client: SupabaseClient,
    limit: int = 40,
    lookback_days: int = 30,
) -> int:
    """Create a first deterministic batch of pending shared-expense suggestions."""

    ratio_other = _safe_decimal(household_link.get("default_split_ratio_other"), fallback=Decimal("0.5"))
    target = _resolve_target(household_link)
    if target is None:
        return 0

    start_date = (date.today() - timedelta(days=max(1, lookback_days))).isoformat()
    rows, _ = supabase_client.get_rows(
        table="releves_bancaires",
        query={
            "select": "id,montant,payee,libelle,categorie,date",
            "profile_id": f"eq.{profile_id}",
            "date": f"gte.{start_date}",
            "order": "date.desc",
            "limit": max(1, min(100, limit * 3)),
        },
        with_count=False,
        use_anon_key=False,
    )

    suggestions: list[dict[str, Any]] = []
    for row in rows:
        if len(suggestions) >= limit:
            break
        transaction_id = _parse_uuid(row.get("id"))
        amount = _safe_decimal(row.get("montant"), fallback=Decimal("0"))
        if transaction_id is None or amount >= Decimal("0"):
            continue

        category = str(row.get("categorie") or row.get("category") or "").strip()
        if category.lower() == "internal transfer":
            continue
        merchant_blob = f"{row.get('payee') or ''} {row.get('libelle') or ''}".lower()
        if any(pattern in merchant_blob for pattern in _INTERNAL_TRANSFER_PATTERNS):
            continue

        score = 0
        if any(keyword in merchant_blob for keyword in _HOUSEHOLD_KEYWORDS):
            score += 2
        if abs(amount) >= Decimal("80"):
            score += 1
        if category.strip().lower() in _HOUSEHOLD_CATEGORIES:
            score += 1
        if score < 2:
            continue

        suggestions.append(
            {
                "transaction_id": transaction_id,
                "suggested_to_profile_id": target["suggested_to_profile_id"],
                "other_party_label": target["other_party_label"],
                "suggested_split_ratio_other": ratio_other,
                "status": "pending",
                "confidence": 0.6,
                "rationale": "auto_seed_after_link_setup",
                "link_id": household_link.get("link_id"),
                "link_pair_id": household_link.get("link_pair_id"),
            }
        )

    return shared_expenses_repository.create_shared_expense_suggestions_bulk(
        profile_id=profile_id,
        suggestions=suggestions,
    )


def _resolve_target(household_link: dict[str, Any]) -> dict[str, Any] | None:
    link_type = str(household_link.get("link_type") or "external")
    if link_type == "internal":
        other_profile_id = _parse_uuid(household_link.get("other_profile_id"))
        if other_profile_id is not None:
            return {"suggested_to_profile_id": other_profile_id, "other_party_label": None}
        label = str(household_link.get("other_party_label") or "").strip()
        if not label:
            return None
        return {"suggested_to_profile_id": None, "other_party_label": label}

    label = str(household_link.get("other_party_label") or "").strip()
    if not label:
        return None
    return {"suggested_to_profile_id": None, "other_party_label": label}


def _parse_uuid(raw_value: Any) -> UUID | None:
    try:
        return raw_value if isinstance(raw_value, UUID) else UUID(str(raw_value))
    except (TypeError, ValueError):
        return None


def _safe_decimal(raw_value: Any, *, fallback: Decimal) -> Decimal:
    try:
        return Decimal(str(raw_value))
    except (InvalidOperation, TypeError, ValueError):
        return fallback
