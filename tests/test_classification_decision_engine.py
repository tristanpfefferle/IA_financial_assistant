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
SYSTEM_CATEGORY = UUID("33333333-3333-3333-3333-333333333333")


@dataclass
class StubRepos:
    merchant_entity_by_alias: dict[str, UUID] = field(default_factory=dict)
    override_by_entity: dict[UUID, UUID] = field(default_factory=dict)
    entity_suggested: dict[UUID, str] = field(default_factory=dict)
    categories_by_name_norm: dict[str, UUID] = field(default_factory=dict)
    created_suggestions: set[str] = field(default_factory=set)
    suggestion_create_calls: int = 0

    def find_merchant_entity_by_alias_norm(self, *, alias_norm: str):
        entity_id = self.merchant_entity_by_alias.get(alias_norm)
        if entity_id is None:
            return None
        return {"id": str(entity_id)}

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

    def create_pending_map_alias_suggestion(
        self,
        *,
        profile_id: UUID,
        observed_alias: str,
        observed_alias_norm: str,
        rationale: str,
        confidence: float,
    ) -> bool:
        del profile_id, observed_alias, rationale, confidence
        if observed_alias_norm in self.created_suggestions:
            return False
        self.created_suggestions.add(observed_alias_norm)
        self.suggestion_create_calls += 1
        return True


def _decide(repos: StubRepos, *, libelle: str, payee: str, montant: str = "-10"):
    return decide_releve_classification(
        profile_id=PROFILE_ID,
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
        merchant_entity_by_alias={"coop monthey": MERCHANT_ENTITY_ID},
        override_by_entity={MERCHANT_ENTITY_ID: CATEGORY_B},
        entity_suggested={MERCHANT_ENTITY_ID: "food"},
        categories_by_name_norm={"food": CATEGORY_A},
    )

    decision = _decide(repos, libelle="Paiement COOP MONTHEY", payee="COOP MONTHEY")

    assert decision.category_id == CATEGORY_B
    assert decision.source.value == "override"


def test_entity_applies_when_no_override() -> None:
    repos = StubRepos(
        merchant_entity_by_alias={"coop monthey": MERCHANT_ENTITY_ID},
        entity_suggested={MERCHANT_ENTITY_ID: "food"},
        categories_by_name_norm={"food": CATEGORY_A},
    )

    decision = _decide(repos, libelle="Paiement COOP MONTHEY", payee="COOP MONTHEY")

    assert decision.category_id == CATEGORY_A
    assert decision.source.value == "entity"


def test_system_applies_when_no_merchant() -> None:
    repos = StubRepos(categories_by_name_norm={"salaire": SYSTEM_CATEGORY})

    decision = _decide(repos, libelle="Salaire janvier", payee="Employeur SA", montant="2500")

    assert decision.category_id == SYSTEM_CATEGORY
    assert decision.source.value == "system"


def test_fallback_creates_pending_suggestion_dedup() -> None:
    repos = StubRepos()

    first = _decide(repos, libelle="Paiement inconnu", payee="MYSTERY SHOP")
    second = _decide(repos, libelle="Paiement inconnu", payee="MYSTERY SHOP")

    assert first.source.value == "fallback"
    assert second.source.value == "fallback"
    assert first.category_id is None
    assert second.category_id is None
    assert repos.suggestion_create_calls == 1
