"""Deterministic classification decision engine for imported bank statements."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import re
import unicodedata
from typing import Any, Protocol
from uuid import UUID

from shared.models import ClassificationDecision, ClassificationSource


class ClassificationDecisionRepositories(Protocol):
    """Repository dependencies required by the classification engine."""

    def get_profile_merchant_override(
        self,
        *,
        profile_id: UUID,
        merchant_entity_id: UUID,
    ) -> dict[str, Any] | None: ...

    def get_merchant_entity_suggested_category_norm(self, *, merchant_entity_id: UUID) -> str | None: ...

    def find_profile_category_id_by_name_norm(self, *, profile_id: UUID, name_norm: str) -> UUID | None: ...

def normalize_merchant_alias(text: str | None) -> str:
    """Normalize merchant alias for deterministic matching and deduplication."""

    raw = str(text or "").strip().lower()
    if not raw:
        return ""
    normalized = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[\.,;:!\?\(\)\[\]\{\}\-_/\\'\"`~]", " ", normalized)
    return " ".join(normalized.split())

def _decision(*, merchant_entity_id: UUID | None, category_id: UUID | None, confidence: float, source: ClassificationSource, rationale: str) -> ClassificationDecision:
    return ClassificationDecision(
        merchant_entity_id=merchant_entity_id,
        category_id=category_id,
        confidence=confidence,
        source=source,
        rationale=rationale,
    )


def decide_releve_classification(
    *,
    profile_id: UUID,
    merchant_entity_id: UUID,
    bank_account_id: UUID | None,
    libelle: str | None,
    payee: str | None,
    montant: Decimal,
    devise: str | None,
    date: date,
    metadata: dict[str, object] | None,
    repositories: ClassificationDecisionRepositories,
) -> ClassificationDecision:
    """Compute deterministic and explainable category decision with strict hierarchy."""

    del bank_account_id, devise, date, libelle, payee, montant, metadata

    override = repositories.get_profile_merchant_override(
        profile_id=profile_id,
        merchant_entity_id=merchant_entity_id,
    )
    if override and override.get("category_id"):
        return _decision(
            merchant_entity_id=merchant_entity_id,
            category_id=UUID(str(override["category_id"])),
            confidence=1.0,
            source=ClassificationSource.OVERRIDE,
            rationale="override profil marchand appliqué",
        )

    suggested_norm = repositories.get_merchant_entity_suggested_category_norm(merchant_entity_id=merchant_entity_id)
    if suggested_norm:
        category_id = repositories.find_profile_category_id_by_name_norm(
            profile_id=profile_id,
            name_norm=suggested_norm,
        )
        if category_id is not None:
            return _decision(
                merchant_entity_id=merchant_entity_id,
                category_id=category_id,
                confidence=0.85,
                source=ClassificationSource.ENTITY,
                rationale="catégorie suggérée par entité marchand",
            )

    return _decision(
        merchant_entity_id=merchant_entity_id,
        category_id=None,
        confidence=0.0,
        source=ClassificationSource.FALLBACK,
        rationale="aucune correspondance déterministe",
    )
