from __future__ import annotations

import base64
from datetime import date
from uuid import UUID, uuid4

from backend.repositories.releves_repository import InMemoryRelevesRepository
from backend.services.classification.decision_engine import normalize_merchant_alias
from backend.services.releves_import.importer import RelevesImportService
from shared.models import RelevesImportFile, RelevesImportMode, RelevesImportModifiedAction, RelevesImportRequest


PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
PROFILE_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1")
PROFILE_B = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2")


class _ProfilesStub:
    def __init__(self, *, with_autres: bool = True) -> None:
        self._categories_by_norm: dict[str, UUID] = {
            "transport": UUID("11111111-1111-1111-1111-111111111111"),
            "alimentation": UUID("22222222-2222-2222-2222-222222222222"),
        }
        if with_autres:
            self._categories_by_norm["autres"] = UUID("99999999-9999-9999-9999-999999999999")

        self.alias_to_entity: dict[str, UUID] = {}
        self.entity_suggested: dict[UUID, str] = {}
        self.profile_entity_overrides: dict[tuple[UUID, UUID], UUID] = {}
        self.created_entity_count = 0
        self.created_alias_count = 0
        self.pending_alias_norms: set[str] = set()

    def ensure_system_categories(self, *, profile_id: UUID, categories: list[dict[str, str]]) -> dict[str, int]:
        del profile_id
        created_count = 0
        for category in categories:
            norm = normalize_merchant_alias(str(category.get("name", "")))
            if norm and norm not in self._categories_by_norm:
                self._categories_by_norm[norm] = uuid4()
                created_count += 1
        return {"created_count": created_count, "system_total_count": len(self._categories_by_norm)}

    def ensure_merchant_entity_from_alias(
        self,
        *,
        profile_id: UUID,
        observed_alias: str,
        observed_alias_norm: str,
        merchant_key_norm: str,
    ) -> UUID:
        del profile_id, observed_alias, merchant_key_norm
        alias_norm = normalize_merchant_alias(observed_alias_norm)
        if not alias_norm:
            raise ValueError("observed_alias_norm must be non-empty")
        existing = self.alias_to_entity.get(alias_norm)
        if existing is not None:
            return existing

        entity_id = uuid4()
        self.alias_to_entity[alias_norm] = entity_id
        self.entity_suggested.setdefault(entity_id, "")
        self.created_entity_count += 1
        self.created_alias_count += 1
        return entity_id

    def find_merchant_entity_by_alias_norm(self, *, alias_norm: str):
        normalized = normalize_merchant_alias(alias_norm)
        entity_id = self.alias_to_entity.get(normalized)
        if entity_id is None:
            return None
        return {"id": str(entity_id)}

    def create_pending_map_alias_suggestion(
        self,
        *,
        profile_id: UUID,
        observed_alias: str,
        observed_alias_norm: str,
        merchant_key_norm: str | None = None,
        rationale: str,
        confidence: float,
    ) -> bool:
        del profile_id, observed_alias, rationale, confidence
        normalized = normalize_merchant_alias(merchant_key_norm or observed_alias_norm)
        if not normalized or normalized in self.pending_alias_norms:
            return False
        self.pending_alias_norms.add(normalized)
        return True

    def find_profile_category_id_by_name_norm(self, *, profile_id: UUID, name_norm: str) -> UUID | None:
        del profile_id
        return self._categories_by_norm.get(normalize_merchant_alias(name_norm))

    def get_profile_merchant_override(self, *, profile_id: UUID, merchant_entity_id: UUID):
        category_id = self.profile_entity_overrides.get((profile_id, merchant_entity_id))
        if category_id is None:
            return None
        return {"category_id": str(category_id)}

    def get_merchant_entity_suggested_category_norm(self, *, merchant_entity_id: UUID):
        suggested = self.entity_suggested.get(merchant_entity_id)
        return suggested if suggested else None


def _build_request(csv_content: bytes, *, profile_id: UUID = PROFILE_ID) -> RelevesImportRequest:
    return RelevesImportRequest(
        profile_id=profile_id,
        files=[
            RelevesImportFile(
                filename="ubs.csv",
                content_base64=base64.b64encode(csv_content).decode("utf-8"),
            )
        ],
        import_mode=RelevesImportMode.COMMIT,
        modified_action=RelevesImportModifiedAction.KEEP,
    )


def _build_unknown_transactions_csv(total: int = 45) -> bytes:
    csv_lines = [
        "Numéro de compte: CH00 0000 0000 0000 0000 0",
        "IBAN: CH00 0000 0000 0000 0000 0",
        "Du: 01.01.2025",
        "Au: 31.01.2025",
        "Date de transaction;Date de comptabilisation;Description1;Description2;Description3;No de transaction;Débit;Crédit;Monnaie",
    ]
    for i in range(1, total + 1):
        tx_date = date(2025, 1, (i % 28) + 1).strftime("%d.%m.%Y")
        csv_lines.append(
            f"{tx_date};{tx_date};Paiement test {i};;;TRX-{i:03d};{(i % 17) + 1},00;;CHF"
        )
    return "\n".join(csv_lines).encode("utf-8")


def _build_single_coop_csv() -> bytes:
    return """Numéro de compte: CH00 0000 0000 0000 0000 0
IBAN: CH00 0000 0000 0000 0000 0
Du: 01.01.2025
Au: 31.01.2025
Date de transaction;Date de comptabilisation;Description1;Description2;Description3;No de transaction;Débit;Crédit;Monnaie
10.01.2025;10.01.2025;COOP MONTHEY;;;TRX-COOP-001;12,50;;CHF
""".encode("utf-8")


def _build_empty_alias_csv() -> bytes:
    return """Numéro de compte: CH00 0000 0000 0000 0000 0
IBAN: CH00 0000 0000 0000 0000 0
Du: 01.01.2025
Au: 31.01.2025
Date de transaction;Date de comptabilisation;Description1;Description2;Description3;No de transaction;Débit;Crédit;Monnaie
10.01.2025;10.01.2025;;;;TRX-EMPTY-001;12,50;;CHF
""".encode("utf-8")




def _build_sumup_alias_variants_csv() -> bytes:
    return """Numéro de compte: CH00 0000 0000 0000 0000 0
IBAN: CH00 0000 0000 0000 0000 0
Du: 01.01.2025
Au: 31.01.2025
Date de transaction;Date de comptabilisation;Description1;Description2;Description3;No de transaction;Débit;Crédit;Monnaie
10.01.2025;10.01.2025;SumUp *L e Scalp Coif 1897 Le Bouveret - 19168190-0 09/28 Paiement carte;;;TRX-SUMUP-001;45,00;;CHF
11.01.2025;11.01.2025;SumUp *L e Scalp Coif 1897 Le Bouveret - [NUM]-0 09/28 Paiement carte;;;TRX-SUMUP-002;38,00;;CHF
""".encode("utf-8")

def test_import_45_unknown_transactions_creates_pending_suggestions_with_null_merchant_links() -> None:
    repository = InMemoryRelevesRepository()
    profiles_repository = _ProfilesStub(with_autres=False)
    service = RelevesImportService(releves_repository=repository, profiles_repository=profiles_repository)

    result = service.import_releves(_build_request(_build_unknown_transactions_csv()))

    assert result.imported_count == 45
    assert result.merchant_suggestions_created_count == 45
    assert profiles_repository.created_entity_count == 0
    assert profiles_repository.created_alias_count == 0

    imported_rows = repository.list_releves_for_import(profile_id=PROFILE_ID, bank_account_id=None)
    rows = [row for row in imported_rows if str(row.get("libelle", "")).startswith("Paiement test")]
    assert len(rows) == 45
    assert all(row.get("merchant_entity_id") is None for row in rows)
    assert all(row.get("category_id") is not None for row in rows)
    assert all(row.get("categorie") is None for row in rows)




def test_import_sumup_alias_variants_creates_single_pending_suggestion_and_reimport_is_idempotent() -> None:
    repository = InMemoryRelevesRepository()
    profiles_repository = _ProfilesStub(with_autres=False)
    service = RelevesImportService(releves_repository=repository, profiles_repository=profiles_repository)

    csv_content = _build_sumup_alias_variants_csv()
    first = service.import_releves(_build_request(csv_content))
    second = service.import_releves(_build_request(csv_content))

    assert first.imported_count == 2
    assert first.merchant_suggestions_created_count == 1
    assert second.imported_count == 0
    assert second.merchant_suggestions_created_count == 0

    imported_rows = repository.list_releves_for_import(profile_id=PROFILE_ID, bank_account_id=None)
    sumup_rows = [
        row
        for row in imported_rows
        if isinstance(row.get("libelle"), str) and str(row.get("libelle", "")).startswith("SumUp *L")
    ]
    assert len(sumup_rows) == 2
    assert {row["meta"].get("observed_alias_key_norm") for row in sumup_rows} == {"le scalp coif le bouveret"}

def test_reimport_same_45_unknown_transactions_creates_no_new_entities_or_aliases() -> None:
    repository = InMemoryRelevesRepository()
    profiles_repository = _ProfilesStub(with_autres=False)
    service = RelevesImportService(releves_repository=repository, profiles_repository=profiles_repository)

    csv_content = _build_unknown_transactions_csv()
    first = service.import_releves(_build_request(csv_content))
    second = service.import_releves(_build_request(csv_content))

    assert first.imported_count == 45
    assert second.imported_count == 0
    assert second.modified_count == 0
    assert first.merchant_suggestions_created_count == 45
    assert second.merchant_suggestions_created_count == 0
    assert profiles_repository.created_entity_count == 0
    assert profiles_repository.created_alias_count == 0
    assert len(profiles_repository.pending_alias_norms) == 45


def test_import_with_empty_alias_does_not_create_pending_suggestion() -> None:
    repository = InMemoryRelevesRepository()
    profiles_repository = _ProfilesStub(with_autres=False)
    service = RelevesImportService(releves_repository=repository, profiles_repository=profiles_repository)

    result = service.import_releves(_build_request(_build_empty_alias_csv()))

    assert result.imported_count == 1
    assert result.failed_count == 0
    assert result.merchant_suggestions_created_count == 0

    imported_rows = repository.list_releves_for_import(profile_id=PROFILE_ID, bank_account_id=None)
    empty_alias_rows = [
        row
        for row in imported_rows
        if isinstance(row.get("meta"), dict)
        and row["meta"].get("_external_id") == "TRX-EMPTY-001"
    ]
    assert len(empty_alias_rows) == 1
    assert empty_alias_rows[0]["merchant_entity_id"] is None
    assert empty_alias_rows[0]["meta"]["merchant_resolution"] == "unresolved_empty_alias"


def test_same_merchant_entity_has_profile_specific_override_categories() -> None:
    repository = InMemoryRelevesRepository()
    profiles_repository = _ProfilesStub()
    service = RelevesImportService(releves_repository=repository, profiles_repository=profiles_repository)

    coop_alias_norm = normalize_merchant_alias("COOP MONTHEY")
    coop_entity = profiles_repository.ensure_merchant_entity_from_alias(
        profile_id=PROFILE_A,
        observed_alias="COOP MONTHEY",
        observed_alias_norm=coop_alias_norm,
        merchant_key_norm=coop_alias_norm,
    )
    profiles_repository.profile_entity_overrides[(PROFILE_A, coop_entity)] = UUID(
        "11111111-1111-1111-1111-111111111111"
    )
    profiles_repository.profile_entity_overrides[(PROFILE_B, coop_entity)] = UUID(
        "22222222-2222-2222-2222-222222222222"
    )

    result_a = service.import_releves(_build_request(_build_single_coop_csv(), profile_id=PROFILE_A))
    result_b = service.import_releves(_build_request(_build_single_coop_csv(), profile_id=PROFILE_B))

    assert result_a.imported_count == 1
    assert result_b.imported_count == 1

    rows_a = repository.list_releves_for_import(profile_id=PROFILE_A, bank_account_id=None)
    rows_b = repository.list_releves_for_import(profile_id=PROFILE_B, bank_account_id=None)

    coop_row_a = [row for row in rows_a if row.get("libelle") == "COOP MONTHEY"][0]
    coop_row_b = [row for row in rows_b if row.get("libelle") == "COOP MONTHEY"][0]

    assert coop_row_a["merchant_entity_id"] == coop_row_b["merchant_entity_id"]
    assert coop_row_a["category_id"] != coop_row_b["category_id"]
