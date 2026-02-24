"""Deterministic seed generator for initial shared-expense suggestions."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from backend.db.supabase_client import SupabaseClient
from backend.repositories.shared_expenses_repository import SharedExpensesRepository

_INTERNAL_TRANSFER_PATTERNS = ("virement", "transfer", "twint p2p")
SHAREABLE_CATEGORIES = {
    "food",
    "housing",
    "insurance",
    "subscriptions",
    "transport",
}

PERSONAL_CATEGORIES = {
    "hobbies",
    "habits",
    "gifts",
}

MIN_CONFIDENCE_THRESHOLD = Decimal("0.6")



_CATEGORY_NORM_FALLBACKS = {
    "alimentation": "food",
    "food": "food",
    "logement": "housing",
    "housing": "housing",
    "assurance": "insurance",
    "insurance": "insurance",
    "abonnements": "subscriptions",
    "subscriptions": "subscriptions",
    "transport": "transport",
    "hobbies": "hobbies",
    "habits": "habits",
    "gifts": "gifts",
}

def compute_share_confidence(*, category_norm: str | None, amount: Decimal) -> tuple[Decimal, str]:
    """Return (confidence, rationale) using deterministic category+amount scoring."""

    score = Decimal("0")
    reasons: list[str] = []

    if category_norm in SHAREABLE_CATEGORIES:
        score += Decimal("0.4")
        reasons.append("shareable_category")

    if category_norm in PERSONAL_CATEGORIES:
        score -= Decimal("0.4")
        reasons.append("personal_category")

    if amount >= Decimal("20"):
        score += Decimal("0.2")
        reasons.append("amount>=20")

    if score < 0:
        score = Decimal("0")
    if score > 1:
        score = Decimal("1")

    rationale = " + ".join(reasons) if reasons else "no_signal"
    return score, rationale


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
            "select": "id,montant,payee,libelle,categorie,category_norm,date",
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
        category_norm = _normalize_category_norm(row.get("category_norm"), fallback_category=category)
        if category.lower() == "internal transfer":
            continue
        merchant_blob = f"{row.get('payee') or ''} {row.get('libelle') or ''}".lower()
        if any(pattern in merchant_blob for pattern in _INTERNAL_TRANSFER_PATTERNS):
            continue

        confidence, rationale = compute_share_confidence(
            category_norm=category_norm,
            amount=abs(amount),
        )
        if confidence < MIN_CONFIDENCE_THRESHOLD:
            continue

        suggestions.append(
            {
                "transaction_id": transaction_id,
                "suggested_to_profile_id": target["suggested_to_profile_id"],
                "other_party_label": target["other_party_label"],
                "suggested_split_ratio_other": ratio_other,
                "status": "pending",
                "confidence": confidence,
                "rationale": rationale,
                "link_id": household_link.get("link_id"),
                "link_pair_id": household_link.get("link_pair_id"),
            }
        )

    return shared_expenses_repository.create_shared_expense_suggestions_bulk(
        profile_id=profile_id,
        suggestions=suggestions,
    )



def _normalize_category_norm(raw_category_norm: Any, *, fallback_category: str) -> str | None:
    normalized = str(raw_category_norm or "").strip().lower()
    if normalized:
        return normalized
    return _CATEGORY_NORM_FALLBACKS.get(fallback_category.strip().lower())


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
