"""Tests for finance_releves_import_files tool."""

from __future__ import annotations

import base64
from pathlib import Path
from uuid import UUID

from agent.backend_client import BackendClient
from agent.tool_router import ToolRouter
from backend.repositories.categories_repository import InMemoryCategoriesRepository
from backend.repositories.releves_repository import InMemoryRelevesRepository
from backend.repositories.transactions_repository import GestionFinanciereTransactionsRepository
from backend.services.classification.recurrence import RecurringCluster
from backend.services.tools import BackendToolService
from shared.models import RelevesImportResult

PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
ACCOUNT_ID = UUID("99999999-9999-9999-9999-999999999999")


class _ImportProfilesRepositoryStub:
    def __init__(self) -> None:
        self.suggestions: list[dict[str, str]] = []
        self.created_entities: list[dict[str, str]] = []

    def ensure_system_categories(self, *, profile_id: UUID, categories: list[dict[str, str]]) -> dict[str, int]:
        del profile_id, categories
        return {"created_count": 0, "system_total_count": 1}

    def find_profile_category_id_by_name_norm(self, *, profile_id: UUID, name_norm: str) -> UUID | None:
        del name_norm
        return UUID("00000000-0000-0000-0000-000000000099") if profile_id else None

    def find_merchant_entity_by_alias_norm(self, *, alias_norm: str):
        del alias_norm
        return None

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
        self.suggestions.append(
            {
                "profile_id": str(profile_id),
                "observed_alias": observed_alias,
                "observed_alias_norm": observed_alias_norm,
                "merchant_key_norm": merchant_key_norm,
                "rationale": rationale,
                "confidence": str(confidence),
            }
        )
        return True


def _build_router() -> ToolRouter:
    service = BackendToolService(
        transactions_repository=GestionFinanciereTransactionsRepository(),
        releves_repository=InMemoryRelevesRepository(),
        categories_repository=InMemoryCategoriesRepository(),
    )
    return ToolRouter(backend_client=BackendClient(tool_service=service))


def _fixture_payload(filename: str = "ubs_sample.csv", content: bytes | None = None) -> dict[str, object]:
    if content is None:
        content = Path("tests/fixtures/ubs_sample.csv").read_bytes()
    encoded = base64.b64encode(content).decode("utf-8")
    return {
        "files": [{"filename": filename, "content_base64": encoded}],
        "import_mode": "analyze",
        "modified_action": "keep",
    }


def test_import_analyze_reports_new_rows_and_requires_confirmation() -> None:
    router = _build_router()

    result = router.call("finance_releves_import_files", _fixture_payload(), profile_id=PROFILE_ID)

    assert isinstance(result, RelevesImportResult)
    assert result.new_count > 0
    assert result.imported_count == 0
    assert result.requires_confirmation is True


def test_import_commit_then_recommit_marks_identical() -> None:
    router = _build_router()

    commit_payload = _fixture_payload()
    commit_payload["import_mode"] = "commit"

    first = router.call("finance_releves_import_files", commit_payload, profile_id=PROFILE_ID)
    second = router.call("finance_releves_import_files", commit_payload, profile_id=PROFILE_ID)

    assert isinstance(first, RelevesImportResult)
    assert isinstance(second, RelevesImportResult)
    assert first.imported_count == first.new_count
    assert second.identical_count >= 1
    assert second.imported_count == 0


def test_modified_line_keep_vs_replace() -> None:
    router = _build_router()

    initial_csv = """Numéro de compte: CH00 0000 0000 0000 0000 0
IBAN: CH00 0000 0000 0000 0000 0
Du: 01.01.2025
Au: 31.01.2025
Date de transaction;Date de comptabilisation;Description1;Description2;Description3;No de transaction;Débit;Crédit;Monnaie
10.01.2025;10.01.2025;Café Central;;;TRX-001;12,50;;CHF
11.01.2025;11.01.2025;Salaire Janvier;;;TRX-002;;2500,00;CHF
""".encode("utf-8")

    initial_commit = _fixture_payload(filename="ubs_with_txn.csv", content=initial_csv)
    initial_commit["import_mode"] = "commit"
    router.call("finance_releves_import_files", initial_commit, profile_id=PROFILE_ID)

    changed_csv = initial_csv.decode("utf-8").replace("TRX-001;12,50", "TRX-001;13,50").encode("utf-8")
    analyze_payload = _fixture_payload(filename="ubs_with_txn.csv", content=changed_csv)
    analyzed = router.call("finance_releves_import_files", analyze_payload, profile_id=PROFILE_ID)

    keep_payload = _fixture_payload(filename="ubs_with_txn.csv", content=changed_csv)
    keep_payload["import_mode"] = "commit"
    keep_payload["modified_action"] = "keep"
    keep_result = router.call("finance_releves_import_files", keep_payload, profile_id=PROFILE_ID)

    replace_payload = _fixture_payload(filename="ubs_with_txn.csv", content=changed_csv)
    replace_payload["import_mode"] = "commit"
    replace_payload["modified_action"] = "replace"
    replace_result = router.call("finance_releves_import_files", replace_payload, profile_id=PROFILE_ID)

    assert isinstance(analyzed, RelevesImportResult)
    assert isinstance(keep_result, RelevesImportResult)
    assert isinstance(replace_result, RelevesImportResult)
    assert analyzed.modified_count > 0
    assert keep_result.replaced_count == 0
    assert replace_result.replaced_count > 0


def test_import_analyze_ubs_same_day_distinct_rows_are_not_marked_duplicates() -> None:
    router = _build_router()

    ubs_collision_fixture = """Numéro de compte: CH00 0000 0000 0000 0000 0
IBAN: CH00 0000 0000 0000 0000 0
Du: 01.01.2025
Au: 31.01.2025
Date de transaction;Date de comptabilisation;Description1;Description2;Description3;Débit;Crédit;Monnaie
10.01.2025;10.01.2025;Paiement carte A;;;12,50;;CHF
10.01.2025;10.01.2025;Paiement carte B;;;7,80;;CHF
""".encode("utf-8")

    result = router.call(
        "finance_releves_import_files",
        _fixture_payload(filename="ubs_collision.csv", content=ubs_collision_fixture),
        profile_id=PROFILE_ID,
    )

    assert isinstance(result, RelevesImportResult)
    assert result.new_count == 2
    assert result.duplicates_count == 0


def test_import_applies_bank_account_id_on_all_rows() -> None:
    router = _build_router()

    payload = _fixture_payload()
    payload["bank_account_id"] = str(ACCOUNT_ID)

    result = router.call("finance_releves_import_files", payload, profile_id=PROFILE_ID)

    assert isinstance(result, RelevesImportResult)
    assert result.preview
    assert all(item.bank_account_id == ACCOUNT_ID for item in result.preview)


def test_import_external_id_not_duplicated_when_bank_account_assignment_changes() -> None:
    router = _build_router()

    csv_content = """Numéro de compte: CH00 0000 0000 0000 0000 0
IBAN: CH00 0000 0000 0000 0000 0
Du: 01.01.2025
Au: 31.01.2025
Date de transaction;Date de comptabilisation;Description1;Description2;Description3;No de transaction;Débit;Crédit;Monnaie
10.01.2025;10.01.2025;Paiement carte;;;TRX-100;12,50;;CHF
""".encode("utf-8")

    first_payload = _fixture_payload(filename="ubs_bank_account_switch.csv", content=csv_content)
    first_payload["import_mode"] = "commit"
    first_result = router.call("finance_releves_import_files", first_payload, profile_id=PROFILE_ID)

    second_payload = _fixture_payload(filename="ubs_bank_account_switch.csv", content=csv_content)
    second_payload["import_mode"] = "commit"
    second_payload["bank_account_id"] = str(ACCOUNT_ID)
    second_result = router.call("finance_releves_import_files", second_payload, profile_id=PROFILE_ID)

    assert isinstance(first_result, RelevesImportResult)
    assert isinstance(second_result, RelevesImportResult)
    assert first_result.imported_count == 1
    assert second_result.new_count == 0
    assert (second_result.identical_count + second_result.modified_count) > 0


def test_import_creates_pending_map_alias_suggestions_when_aliases_are_unknown() -> None:
    profiles_repository = _ImportProfilesRepositoryStub()
    service = BackendToolService(
        transactions_repository=GestionFinanciereTransactionsRepository(),
        releves_repository=InMemoryRelevesRepository(),
        categories_repository=InMemoryCategoriesRepository(),
        profiles_repository=profiles_repository,
    )
    router = ToolRouter(backend_client=BackendClient(tool_service=service))

    payload = _fixture_payload(filename="ubs_unknown_aliases.csv")
    payload["import_mode"] = "commit"

    result = router.call("finance_releves_import_files", payload, profile_id=PROFILE_ID)

    assert isinstance(result, RelevesImportResult)
    assert result.new_count > 0
    assert result.merchant_suggestions_created_count > 0
    assert profiles_repository.suggestions
    assert profiles_repository.created_entities == []


class _TransactionClustersRepositoryStub:
    def __init__(self) -> None:
        self.upsert_calls: list[dict[str, object]] = []

    def upsert_cluster(
        self,
        *,
        profile_id: str,
        cluster_type: str,
        cluster_key: str,
        stats: dict[str, object],
        transaction_ids: list[str],
    ) -> str:
        self.upsert_calls.append(
            {
                "profile_id": profile_id,
                "cluster_type": cluster_type,
                "cluster_key": cluster_key,
                "stats": stats,
                "transaction_ids": transaction_ids,
            }
        )
        return "cluster-1"


def test_import_commit_detects_and_persists_recurring_clusters(monkeypatch) -> None:
    releves_repository = InMemoryRelevesRepository()
    clusters_repository = _TransactionClustersRepositoryStub()
    service = BackendToolService(
        transactions_repository=GestionFinanciereTransactionsRepository(),
        releves_repository=releves_repository,
        categories_repository=InMemoryCategoriesRepository(),
        transaction_clusters_repository=clusters_repository,
    )
    router = ToolRouter(backend_client=BackendClient(tool_service=service))

    csv_content = """Numéro de compte: CH00 0000 0000 0000 0000 0
IBAN: CH00 0000 0000 0000 0000 0
Du: 01.01.2025
Au: 31.05.2025
Date de transaction;Date de comptabilisation;Description1;Description2;Description3;No de transaction;Débit;Crédit;Monnaie
05.01.2025;05.01.2025;Netflix;;;TRX-1;20,00;;CHF
05.02.2025;05.02.2025;Netflix;;;TRX-2;20,00;;CHF
05.03.2025;05.03.2025;Netflix;;;TRX-3;20,00;;CHF
05.04.2025;05.04.2025;Netflix;;;TRX-4;20,00;;CHF
""".encode("utf-8")

    payload = _fixture_payload(filename="ubs_recurring.csv", content=csv_content)
    payload["import_mode"] = "commit"

    def _fake_detect(_rows):
        imported_ids = [str(item.id) for item in releves_repository._seed if item.profile_id == PROFILE_ID][-4:]
        return [
            RecurringCluster(
                cluster_key="cluster-netflix",
                sign="expense",
                amount_chf=20,
                label_key="netflix",
                transaction_ids=imported_ids,
                stats={"count": 4},
            )
        ]

    monkeypatch.setattr("backend.services.releves_import.importer.detect_monthly_recurring_clusters", _fake_detect)

    result = router.call("finance_releves_import_files", payload, profile_id=PROFILE_ID)

    assert isinstance(result, RelevesImportResult)
    assert result.imported_count == 4
    assert result.recurring_clusters_detected == 1
    assert len(clusters_repository.upsert_calls) == 1
    call = clusters_repository.upsert_calls[0]
    assert call["cluster_type"] == "recurring"
    assert len(call["transaction_ids"]) == 4


def test_import_commit_detects_recurring_cluster_from_historical_rows(monkeypatch) -> None:
    releves_repository = InMemoryRelevesRepository()
    clusters_repository = _TransactionClustersRepositoryStub()
    service = BackendToolService(
        transactions_repository=GestionFinanciereTransactionsRepository(),
        releves_repository=releves_repository,
        categories_repository=InMemoryCategoriesRepository(),
        transaction_clusters_repository=clusters_repository,
    )
    router = ToolRouter(backend_client=BackendClient(tool_service=service))

    jan_to_mar = """Numéro de compte: CH00 0000 0000 0000 0000 0
IBAN: CH00 0000 0000 0000 0000 0
Du: 01.01.2025
Au: 31.03.2025
Date de transaction;Date de comptabilisation;Description1;Description2;Description3;No de transaction;Débit;Crédit;Monnaie
05.01.2025;05.01.2025;Netflix;;;TRX-1;20,00;;CHF
05.02.2025;05.02.2025;Netflix;;;TRX-2;20,00;;CHF
05.03.2025;05.03.2025;Netflix;;;TRX-3;20,00;;CHF
""".encode("utf-8")
    seed_payload = _fixture_payload(filename="ubs_seed_q1.csv", content=jan_to_mar)
    seed_payload["import_mode"] = "commit"
    seeded = router.call("finance_releves_import_files", seed_payload, profile_id=PROFILE_ID)
    assert isinstance(seeded, RelevesImportResult)
    assert seeded.imported_count == 3
    clusters_repository.upsert_calls.clear()

    april_only = """Numéro de compte: CH00 0000 0000 0000 0000 0
IBAN: CH00 0000 0000 0000 0000 0
Du: 01.04.2025
Au: 30.04.2025
Date de transaction;Date de comptabilisation;Description1;Description2;Description3;No de transaction;Débit;Crédit;Monnaie
05.04.2025;05.04.2025;Netflix;;;TRX-4;20,00;;CHF
""".encode("utf-8")
    payload = _fixture_payload(filename="ubs_april_only.csv", content=april_only)
    payload["import_mode"] = "commit"

    def _fake_detect(rows):
        netflix_rows = [row for row in rows if str(row.get("libelle") or "").lower() == "netflix"]
        assert len(netflix_rows) == 4
        return [
            RecurringCluster(
                cluster_key="cluster-netflix",
                sign="expense",
                amount_chf=20,
                label_key="netflix",
                transaction_ids=[str(row["id"]) for row in netflix_rows],
                stats={"count": 4},
            )
        ]

    monkeypatch.setattr("backend.services.releves_import.importer.detect_monthly_recurring_clusters", _fake_detect)

    result = router.call("finance_releves_import_files", payload, profile_id=PROFILE_ID)

    assert isinstance(result, RelevesImportResult)
    assert result.imported_count == 1
    assert result.recurring_clusters_detected == 1
    assert len(clusters_repository.upsert_calls) == 1
    call = clusters_repository.upsert_calls[0]
    assert call["cluster_type"] == "recurring"
    assert len(call["transaction_ids"]) == 4
