from __future__ import annotations

import base64
from uuid import UUID

from backend.repositories.releves_repository import InMemoryRelevesRepository
from backend.services.releves_import.importer import RelevesImportService
from shared.models import RelevesFilters, RelevesImportFile, RelevesImportMode, RelevesImportModifiedAction, RelevesImportRequest


PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def test_import_twint_p2p_sets_pending_category_and_status() -> None:
    repository = InMemoryRelevesRepository()
    service = RelevesImportService(releves_repository=repository)

    csv_content = """Numéro de compte: CH00 0000 0000 0000 0000 0
IBAN: CH00 0000 0000 0000 0000 0
Du: 01.01.2025
Au: 31.01.2025
Date de transaction;Date de comptabilisation;Description1;Description2;Description3;No de transaction;Débit;Crédit;Monnaie
10.01.2025;10.01.2025;TWINT envoi à Martin Dupont;;;TRX-TWINT-001;20,00;;CHF
""".encode("utf-8")

    request = RelevesImportRequest(
        profile_id=PROFILE_ID,
        files=[
            RelevesImportFile(
                filename="ubs_twint.csv",
                content_base64=base64.b64encode(csv_content).decode("utf-8"),
            )
        ],
        import_mode=RelevesImportMode.COMMIT,
        modified_action=RelevesImportModifiedAction.KEEP,
    )

    result = service.import_releves(request)

    assert result.imported_count == 1

    twint_category: str | None = None
    try:
        list_releves = getattr(repository, "list_releves")
        list_result = list_releves(filters=RelevesFilters(profile_id=PROFILE_ID, limit=500, offset=0))
        releves = list_result[0] if isinstance(list_result, tuple) else list_result
        twint_rows = [row for row in releves if row.libelle == "TWINT envoi à Martin Dupont"]
        if len(twint_rows) == 1:
            twint_category = twint_rows[0].categorie
    except Exception:
        twint_category = None

    imported_rows = repository.list_releves_for_import(profile_id=PROFILE_ID, bank_account_id=None)
    imported_twint_rows = [row for row in imported_rows if row.get("libelle") == "TWINT envoi à Martin Dupont"]
    assert len(imported_twint_rows) == 1

    if twint_category is None:
        raw_category = imported_twint_rows[0].get("categorie")
        twint_category = raw_category if isinstance(raw_category, str) else None

    assert twint_category == "À catégoriser (TWINT)"

    twint_meta = imported_twint_rows[0]["meta"]
    assert isinstance(twint_meta, dict)
    assert twint_meta["category_key"] == "twint_p2p_pending"
    assert twint_meta["category_status"] == "pending"
    assert twint_meta["tx_kind"] in {"expense", "income"}
    assert twint_meta["tx_kind"] == "expense"
