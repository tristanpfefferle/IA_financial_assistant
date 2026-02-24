"""Regression tests for category aggregation label resolution."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from backend.repositories.releves_repository import SupabaseRelevesRepository
from shared.models import RelevesAggregateRequest, RelevesGroupBy


class _ClientStub:
    def get_rows(self, *, table, query, with_count, use_anon_key=False):
        assert table == "releves_bancaires"
        return (
            [
                {
                    "montant": "-42.00",
                    "devise": "CHF",
                    "date": "2026-01-05",
                    "categorie": None,
                    "category_id": "11111111-2222-3333-4444-555555555555",
                    "payee": "Loyer",
                    "metadonnees": {"category_key": "other"},
                }
            ],
            None,
        )


class _ProfilesRepositoryStub:
    def get_profile_category_name_by_id(self, *, profile_id: UUID, category_id: UUID) -> str | None:
        assert profile_id == UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        assert category_id == UUID("11111111-2222-3333-4444-555555555555")
        return "Logement"


def test_aggregate_categories_prefers_category_id_over_other_system_key() -> None:
    repository = SupabaseRelevesRepository(client=_ClientStub(), profiles_repository=_ProfilesRepositoryStub())

    groups, currency = repository.aggregate_releves(
        RelevesAggregateRequest(
            profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            group_by=RelevesGroupBy.CATEGORIE,
        )
    )

    assert currency == "CHF"
    assert groups == {"Logement": (Decimal("-42.00"), 1)}
