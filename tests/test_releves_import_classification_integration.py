from __future__ import annotations

import base64
from datetime import date
from uuid import UUID, uuid4

from backend.repositories.releves_repository import InMemoryRelevesRepository
from backend.services.releves_import.importer import RelevesImportService
from shared.models import RelevesImportFile, RelevesImportMode, RelevesImportModifiedAction, RelevesImportRequest


PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


class _ProfilesStub:
    def __init__(self, *, with_autres: bool = True) -> None:
        self._categories_by_norm: dict[str, UUID] = {
            "a categoriser twint": UUID("88888888-8888-8888-8888-888888888888"),
        }
        if with_autres:
            self._categories_by_norm["autres"] = UUID("99999999-9999-9999-9999-999999999999")

    def ensure_system_categories(self, *, profile_id: UUID, categories: list[dict[str, str]]) -> dict[str, int]:
        del profile_id
        created_count = 0
        for category in categories:
            norm = str(category.get("name", "")).strip().lower()
            if norm and norm not in self._categories_by_norm:
                self._categories_by_norm[norm] = uuid4()
                created_count += 1
        return {"created_count": created_count, "system_total_count": len(self._categories_by_norm)}

    def find_profile_category_id_by_name_norm(self, *, profile_id: UUID, name_norm: str) -> UUID | None:
        del profile_id
        return self._categories_by_norm.get(name_norm.strip().lower())

    def find_merchant_entity_by_alias_norm(self, *, alias_norm: str):
        del alias_norm
        return None

    def get_profile_merchant_override(self, *, profile_id: UUID, merchant_entity_id: UUID):
        del profile_id, merchant_entity_id
        return None

    def get_merchant_entity_suggested_category_norm(self, *, merchant_entity_id: UUID):
        del merchant_entity_id
        return None


def _build_request(csv_content: bytes) -> RelevesImportRequest:
    return RelevesImportRequest(
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


def test_import_twint_p2p_sets_category_id_and_no_free_text_category() -> None:
    repository = InMemoryRelevesRepository()
    service = RelevesImportService(releves_repository=repository, profiles_repository=_ProfilesStub())

    csv_content = """Numéro de compte: CH00 0000 0000 0000 0000 0
IBAN: CH00 0000 0000 0000 0000 0
Du: 01.01.2025
Au: 31.01.2025
Date de transaction;Date de comptabilisation;Description1;Description2;Description3;No de transaction;Débit;Crédit;Monnaie
10.01.2025;10.01.2025;TWINT envoi à Martin Dupont;;;TRX-TWINT-001;20,00;;CHF
""".encode("utf-8")

    result = service.import_releves(_build_request(csv_content))

    assert result.imported_count == 1
    imported_rows = repository.list_releves_for_import(profile_id=PROFILE_ID, bank_account_id=None)
    twint_rows = [row for row in imported_rows if row.get("libelle") == "TWINT envoi à Martin Dupont"]
    assert len(twint_rows) == 1
    assert twint_rows[0].get("category_id") is not None
    assert twint_rows[0].get("categorie") is None


def test_import_45_transactions_sets_non_null_category_id_without_text_category() -> None:
    repository = InMemoryRelevesRepository()
    profiles_repository = _ProfilesStub(with_autres=False)
    service = RelevesImportService(releves_repository=repository, profiles_repository=profiles_repository)

    csv_lines = [
        "Numéro de compte: CH00 0000 0000 0000 0000 0",
        "IBAN: CH00 0000 0000 0000 0000 0",
        "Du: 01.01.2025",
        "Au: 31.01.2025",
        "Date de transaction;Date de comptabilisation;Description1;Description2;Description3;No de transaction;Débit;Crédit;Monnaie",
    ]
    for i in range(1, 46):
        tx_date = date(2025, 1, (i % 28) + 1).strftime("%d.%m.%Y")
        csv_lines.append(
            f"{tx_date};{tx_date};Paiement test {i};;;TRX-{i:03d};{(i % 17) + 1},00;;CHF"
        )

    csv_content = "\n".join(csv_lines).encode("utf-8")
    result = service.import_releves(_build_request(csv_content))

    assert result.imported_count == 45
    assert profiles_repository.find_profile_category_id_by_name_norm(
        profile_id=PROFILE_ID,
        name_norm="autres",
    ) is not None

    imported_rows = repository.list_releves_for_import(profile_id=PROFILE_ID, bank_account_id=None)
    rows = [row for row in imported_rows if str(row.get("libelle", "")).startswith("Paiement test")]
    assert len(rows) == 45
    assert all(row.get("category_id") is not None for row in rows)
    assert all(row.get("categorie") is None for row in rows)
