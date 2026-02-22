"""Deterministic classification decision engine for imported bank statements."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import re
import unicodedata
from typing import Any, Protocol
from uuid import UUID

from shared.models import ClassificationDecision, ClassificationSource


class ClassificationDecisionRepositories(Protocol):
    """Repository dependencies required by the classification engine."""

    def find_merchant_entity_by_alias_norm(self, *, alias_norm: str) -> dict[str, Any] | None: ...

    def get_profile_merchant_override(
        self,
        *,
        profile_id: UUID,
        merchant_entity_id: UUID,
    ) -> dict[str, Any] | None: ...

    def get_merchant_entity_suggested_category_norm(self, *, merchant_entity_id: UUID) -> str | None: ...

    def find_profile_category_id_by_name_norm(self, *, profile_id: UUID, name_norm: str) -> UUID | None: ...

    def create_pending_map_alias_suggestion(
        self,
        *,
        profile_id: UUID,
        observed_alias: str,
        observed_alias_norm: str,
        rationale: str,
        confidence: float,
    ) -> bool: ...


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


def _resolve_system_category(
    *,
    profile_id: UUID,
    amount: Decimal,
    libelle: str,
    payee: str,
    repositories: ClassificationDecisionRepositories,
) -> UUID | None:
    text = normalize_merchant_alias(f"{libelle} {payee}")
    system_name_norm: str | None = None
    if any(marker in text for marker in ("virement interne", "transfert interne", "internal transfer")):
        system_name_norm = "transferts internes"
    elif "twint" in text and any(marker in text for marker in ("envoi", "transfert", "p2p", "peer")):
        system_name_norm = "a categoriser twint"
    elif amount > 0 and any(marker in text for marker in ("salaire", "salary", "payroll", "lohn")):
        system_name_norm = "salaire"
    elif amount < 0 and any(marker in text for marker in ("frais", "fee", "commission", "cotisation")):
        system_name_norm = "frais bancaires"

    if system_name_norm is None:
        return None

    return repositories.find_profile_category_id_by_name_norm(
        profile_id=profile_id,
        name_norm=system_name_norm,
    )


def decide_releve_classification(
    *,
    profile_id: UUID,
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

    del bank_account_id, devise, date, metadata

    normalized_alias = normalize_merchant_alias(payee or libelle)
    entity = repositories.find_merchant_entity_by_alias_norm(alias_norm=normalized_alias) if normalized_alias else None
    merchant_entity_id = UUID(str(entity["id"])) if entity and entity.get("id") else None

    if merchant_entity_id is not None:
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

    if merchant_entity_id is not None:
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

    system_category_id = _resolve_system_category(
        profile_id=profile_id,
        amount=montant,
        libelle=libelle or "",
        payee=payee or "",
        repositories=repositories,
    )
    if system_category_id is not None:
        return _decision(
            merchant_entity_id=merchant_entity_id,
            category_id=system_category_id,
            confidence=0.7,
            source=ClassificationSource.SYSTEM,
            rationale="règle système import appliquée",
        )

    if normalized_alias:
        repositories.create_pending_map_alias_suggestion(
            profile_id=profile_id,
            observed_alias=(payee or libelle or "").strip() or normalized_alias,
            observed_alias_norm=normalized_alias,
            rationale="alias marchand inconnu lors import",
            confidence=0.2,
        )

    return _decision(
        merchant_entity_id=merchant_entity_id,
        category_id=None,
        confidence=0.0,
        source=ClassificationSource.FALLBACK,
        rationale="aucune correspondance déterministe",
    )
