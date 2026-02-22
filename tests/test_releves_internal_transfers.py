from datetime import date
from decimal import Decimal
from uuid import UUID

from backend.repositories.releves_repository import InMemoryRelevesRepository
from shared.models import RelevesAggregateRequest, RelevesDirection, RelevesFilters, RelevesGroupBy


PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def test_sum_and_aggregate_exclude_internal_transfers_but_keep_twint_pending() -> None:
    repository = InMemoryRelevesRepository()
    repository._seed = []
    repository.insert_releves_bulk(
        profile_id=PROFILE_ID,
        rows=[
            {
                "date": date.fromisoformat("2025-02-01"),
                "montant": Decimal("-10.00"),
                "devise": "CHF",
                "libelle": "Sandwich",
                "payee": "Boulangerie",
                "categorie": "Alimentation",
                "meta": {},
            },
            {
                "date": date.fromisoformat("2025-02-02"),
                "montant": Decimal("-40.00"),
                "devise": "CHF",
                "libelle": "Virement vers épargne",
                "payee": "Mon compte",
                "categorie": "Transferts internes",
                "meta": {"tx_kind": "transfer_internal"},
            },
            {
                "date": date.fromisoformat("2025-02-03"),
                "montant": Decimal("-20.00"),
                "devise": "CHF",
                "libelle": "TWINT Anna",
                "payee": "Anna",
                "categorie": "À catégoriser (TWINT)",
                "meta": {"category_status": "pending", "category_key": "twint_p2p_pending"},
            },
        ],
    )

    total, count, _ = repository.sum_releves(
        RelevesFilters(
            profile_id=PROFILE_ID,
            direction=RelevesDirection.DEBIT_ONLY,
        )
    )

    assert total == Decimal("-30.00")
    assert count == 2

    groups, _ = repository.aggregate_releves(
        RelevesAggregateRequest(
            profile_id=PROFILE_ID,
            group_by=RelevesGroupBy.CATEGORIE,
            direction=RelevesDirection.DEBIT_ONLY,
        )
    )

    assert "Transferts internes" not in groups
    assert set(groups.keys()) == {"Alimentation", "À catégoriser (TWINT)"}
    assert groups["Alimentation"] == (Decimal("-10.00"), 1)
    assert groups["À catégoriser (TWINT)"] == (Decimal("-20.00"), 1)
