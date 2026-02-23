from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID

from backend.services.classification.decision_engine import decide_releve_classification


PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
MERCHANT_ENTITY_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
CATEGORY_A = UUID("11111111-1111-1111-1111-111111111111")
CATEGORY_B = UUID("22222222-2222-2222-2222-222222222222")


@dataclass
class StubRepos:
    override_by_entity: dict[UUID, UUID] = field(default_factory=dict)
    entity_suggested: dict[UUID, str] = field(default_factory=dict)
    categories_by_name_norm: dict[str, UUID] = field(default_factory=dict)

    def get_profile_merchant_override(self, *, profile_id: UUID, merchant_entity_id: UUID):
        del profile_id
        category_id = self.override_by_entity.get(merchant_entity_id)
        if category_id is None:
            return None
        return {"category_id": str(category_id)}

    def get_merchant_entity_suggested_category_norm(self, *, merchant_entity_id: UUID):
        return self.entity_suggested.get(merchant_entity_id)

    def find_profile_category_id_by_name_norm(self, *, profile_id: UUID, name_norm: str):
        del profile_id
        return self.categories_by_name_norm.get(name_norm)


def _decide(repos: StubRepos, *, libelle: str, payee: str, montant: str = "-10"):
    return decide_releve_classification(
        profile_id=PROFILE_ID,
        merchant_entity_id=MERCHANT_ENTITY_ID,
        bank_account_id=None,
        libelle=libelle,
        payee=payee,
        montant=Decimal(montant),
        devise="CHF",
        date=date(2025, 1, 1),
        metadata=None,
        repositories=repos,
    )


def test_override_beats_entity_suggested() -> None:
    repos = StubRepos(
        override_by_entity={MERCHANT_ENTITY_ID: CATEGORY_B},
        entity_suggested={MERCHANT_ENTITY_ID: "food"},
        categories_by_name_norm={"food": CATEGORY_A},
    )

    decision = _decide(repos, libelle="Paiement COOP MONTHEY", payee="COOP MONTHEY")

    assert decision.category_id == CATEGORY_B
    assert decision.source.value == "override"


def test_entity_applies_when_no_override() -> None:
    repos = StubRepos(
        entity_suggested={MERCHANT_ENTITY_ID: "food"},
        categories_by_name_norm={"food": CATEGORY_A},
    )

    decision = _decide(repos, libelle="Paiement COOP MONTHEY", payee="COOP MONTHEY")

    assert decision.category_id == CATEGORY_A
    assert decision.source.value == "entity"


def test_fallback_stays_unclassified_without_side_effects() -> None:
    repos = StubRepos()

    first = _decide(repos, libelle="Paiement inconnu", payee="MYSTERY SHOP")
    second = _decide(repos, libelle="Paiement inconnu", payee="MYSTERY SHOP")

    assert first.source.value == "fallback"
    assert second.source.value == "fallback"
    assert first.category_id is None
    assert second.category_id is None
