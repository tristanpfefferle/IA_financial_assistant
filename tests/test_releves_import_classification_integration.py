from __future__ import annotations

import base64
from uuid import UUID

from backend.repositories.releves_repository import InMemoryRelevesRepository
from backend.services.releves_import.importer import RelevesImportService
from shared.models import RelevesImportFile, RelevesImportMode, RelevesImportModifiedAction, RelevesImportRequest


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
    imported_rows = repository.list_releves_for_import(profile_id=PROFILE_ID, bank_account_id=None)
    twint_rows = [row for row in imported_rows if row.get("libelle") == "TWINT envoi à Martin Dupont"]
    assert len(twint_rows) == 1
    assert twint_rows[0]["categorie"] == "À catégoriser (TWINT)"
    assert twint_rows[0]["meta"]["category_key"] == "twint_p2p_pending"
    assert twint_rows[0]["meta"]["category_status"] == "pending"
