from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

import agent.api as agent_api
from agent.api import app
from shared.models import RelevesImportMode


@dataclass
class _Job:
    id: UUID
    profile_id: UUID
    status: str = "pending"
    error_message: str | None = None
    updated_at: datetime | None = None
    total_transactions: int | None = None
    processed_transactions: int | None = None
    total_llm_items: int | None = None
    processed_llm_items: int | None = None
    result: dict[str, Any] | None = None


@dataclass
class _Event:
    seq: int
    kind: str
    message: str
    progress: float | None
    payload: dict[str, Any] | None


class _Repo:
    def __init__(self) -> None:
        self.jobs: dict[UUID, _Job] = {}
        self.events: dict[UUID, list[_Event]] = {}

    def create_job(self, *, profile_id: UUID) -> UUID:
        job_id = uuid4()
        self.jobs[job_id] = _Job(id=job_id, profile_id=profile_id, updated_at=datetime.now(timezone.utc))
        self.events[job_id] = []
        return job_id

    def get_job(self, *, profile_id: UUID, job_id: UUID):
        job = self.jobs.get(job_id)
        if job is None or job.profile_id != profile_id:
            return None
        return job

    def patch_job(self, *, profile_id: UUID, job_id: UUID, payload: dict[str, Any]) -> None:
        job = self.jobs[job_id]
        if job.profile_id != profile_id:
            return
        for key, value in payload.items():
            setattr(job, key, value)
        job.updated_at = datetime.now(timezone.utc)

    def next_event_seq(self, *, job_id: UUID) -> int:
        return len(self.events[job_id]) + 1

    def create_event(self, *, job_id: UUID, seq: int, kind: str, message: str, progress: float | None, payload: dict[str, Any] | None):
        event = _Event(seq=seq, kind=kind, message=message, progress=progress, payload=payload)
        self.events[job_id].append(event)
        return event

    def list_events_since(self, *, job_id: UUID, after_seq: int, limit: int = 200):
        return [event for event in self.events[job_id] if event.seq > after_seq][:limit]


def test_import_job_endpoints_create_upload_and_events(monkeypatch) -> None:
    auth_user_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(auth_user_id), "email": "user@example.com"},
    )

    class _ProfilesRepo:
        def __init__(self) -> None:
            self.chat_state: dict[str, Any] = {
                "state": {
                    "global_state": {
                        "mode": "onboarding",
                        "onboarding_step": "import",
                        "onboarding_substep": "import_wait_ready",
                    }
                }
            }

        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id
            return profile_id

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID) -> dict[str, Any]:
            assert profile_id
            assert user_id
            return self.chat_state

        def update_chat_state(self, *, profile_id: UUID, user_id: UUID, chat_state: dict[str, Any]) -> None:
            assert profile_id
            assert user_id
            self.chat_state = chat_state

    profiles_repo = _ProfilesRepo()

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: profiles_repo)

    captured_import_mode: RelevesImportMode | None = None

    class _BackendClient:
        def finance_releves_import_files(self, *, request: Any, on_progress: Any):
            nonlocal captured_import_mode
            assert request.files[0].filename == "sample.csv"
            assert str(request.bank_account_id) == "11111111-1111-1111-1111-111111111111"
            captured_import_mode = request.import_mode
            on_progress("parsed_total", 47, 47)
            on_progress("categorization", 47, 47)
            return {
                "imported_count": 2,
                "preview": [
                    {"date": "2026-01-01", "montant": "10.00", "devise": "EUR"},
                    {"date": "2026-01-31", "montant": "20.00", "devise": "EUR"},
                ],
            }

    class _Router:
        def __init__(self) -> None:
            self.backend_client = _BackendClient()

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    repo = _Repo()
    monkeypatch.setattr(agent_api, "_get_import_jobs_repository_or_501", lambda: repo)

    client = TestClient(app)
    headers = {"Authorization": "Bearer token"}

    create_response = client.post("/imports/jobs", headers=headers)
    assert create_response.status_code == 200
    job_id = create_response.json()["job_id"]

    upload_response = client.post(
        f"/imports/jobs/{job_id}/files",
        headers=headers,
        json={
            "files": [{"filename": "sample.csv", "content_base64": "ZGF0ZSxtb250YW50XG4yMDI2LTAxLTAxLDEw"}],
            "bank_account_id": "11111111-1111-1111-1111-111111111111",
        },
    )
    assert upload_response.status_code == 200
    assert upload_response.json()["ok"] is True

    status_response = client.get(f"/imports/jobs/{job_id}", headers=headers)
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "done"
    assert captured_import_mode == RelevesImportMode.COMMIT
    assert repo.jobs[UUID(job_id)].result is not None
    assert repo.jobs[UUID(job_id)].result["bank_account_id"] == "11111111-1111-1111-1111-111111111111"
    assert repo.jobs[UUID(job_id)].result["import_start_date"] == "2026-01-01"
    assert repo.jobs[UUID(job_id)].result["import_end_date"] == "2026-01-31"

    finalize_response = client.post(f"/imports/jobs/{job_id}/finalize-chat", headers=headers)
    assert finalize_response.status_code == 200
    payload = finalize_response.json()
    assert payload["reply"] == (
        "Import terminé ✅\n\n"
        "Je viens de classer tes 47 transactions et de générer ton premier rapport financier.\n\n"
        "Es-tu prêt à le découvrir ?"
    )
    assert "(oui/non)" not in payload["reply"]
    assert payload["tool_result"]["type"] == "ui_action"
    assert payload["tool_result"]["action"] == "quick_replies"
    assert len(payload["tool_result"]["options"]) == 2

    events = repo.events[UUID(job_id)]
    kinds = [event.kind for event in events]
    assert "started" in kinds
    assert "parsing" in kinds
    assert "parsed" in kinds
    assert "db_insert_progress" in kinds
    parsed_events = [event for event in events if event.kind == "parsed"]
    assert parsed_events
    assert parsed_events[0].message == "Transactions détectées : 47."
    first_categorization = next(event for event in events if event.kind == "categorization_progress")
    assert first_categorization.message == "Catégorisation… (0/47)"
    parsed_index = next(index for index, event in enumerate(events) if event.kind == "parsed")
    first_categorization_index = next(index for index, event in enumerate(events) if event.kind == "categorization_progress")
    assert parsed_index < first_categorization_index
    done_event = next(event for event in events if event.kind == "done")
    assert done_event.message == "Traitement terminé."
    assert "done" in kinds

    persisted_global_state = profiles_repo.chat_state.get("state", {}).get("global_state", {})
    assert persisted_global_state.get("onboarding_step") == "report"
    assert persisted_global_state.get("onboarding_substep") == "report_offer"
    persisted_last_query = profiles_repo.chat_state.get("state", {}).get("last_query", {})
    date_range = persisted_last_query.get("filters", {}).get("date_range", {})
    assert date_range == {"start_date": "2026-01-01", "end_date": "2026-01-31"}


def test_import_job_pipeline_marks_error_on_tool_error_payload(monkeypatch) -> None:
    auth_user_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(auth_user_id), "email": "user@example.com"},
    )

    class _ProfilesRepo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id
            return profile_id

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID) -> dict[str, Any]:
            assert profile_id
            assert user_id
            return {"state": {}}

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _ProfilesRepo())

    class _BackendClient:
        def finance_releves_import_files(self, *, request: Any, on_progress: Any):
            assert request.files[0].filename == "sample.csv"
            return {"code": "validation_error", "message": "bank account missing"}

    class _Router:
        def __init__(self) -> None:
            self.backend_client = _BackendClient()

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    repo = _Repo()
    monkeypatch.setattr(agent_api, "_get_import_jobs_repository_or_501", lambda: repo)

    client = TestClient(app)
    headers = {"Authorization": "Bearer token"}

    create_response = client.post("/imports/jobs", headers=headers)
    assert create_response.status_code == 200
    job_id = create_response.json()["job_id"]

    upload_response = client.post(
        f"/imports/jobs/{job_id}/files",
        headers=headers,
        json={"files": [{"filename": "sample.csv", "content_base64": "ZGF0ZSxtb250YW50XG4yMDI2LTAxLTAxLDEw"}]},
    )
    assert upload_response.status_code == 200
    assert upload_response.json()["ok"] is True

    status_response = client.get(f"/imports/jobs/{job_id}", headers=headers)
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "error"

    events = repo.events[UUID(job_id)]
    kinds = [event.kind for event in events]
    assert "error" in kinds
    assert "done" not in kinds
    error_event = next(event for event in events if event.kind == "error")
    assert "bank account missing" in error_event.message

    finalize_response = client.post(f"/imports/jobs/{job_id}/finalize-chat", headers=headers)
    assert finalize_response.status_code == 409



def test_import_job_pipeline_auto_selects_detected_bank_account(monkeypatch) -> None:
    auth_user_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(auth_user_id), "email": "user@example.com"},
    )

    class _ProfilesRepo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id
            return profile_id

        def list_bank_accounts(self, *, profile_id: UUID):
            assert profile_id
            return [
                {"id": "11111111-1111-1111-1111-111111111111", "name": "UBS"},
                {"id": "22222222-2222-2222-2222-222222222222", "name": "Revolut"},
            ]

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID) -> dict[str, Any]:
            return {"state": {}}

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _ProfilesRepo())

    captured_request: dict[str, Any] = {}

    class _BackendClient:
        def finance_releves_import_files(self, *, request: Any, on_progress: Any):
            captured_request["bank_account_id"] = str(request.bank_account_id) if request.bank_account_id else None
            return {"imported_count": 1}

    class _Router:
        def __init__(self) -> None:
            self.backend_client = _BackendClient()

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    repo = _Repo()
    monkeypatch.setattr(agent_api, "_get_import_jobs_repository_or_501", lambda: repo)

    client = TestClient(app)
    headers = {"Authorization": "Bearer token"}

    create_response = client.post("/imports/jobs", headers=headers)
    job_id = create_response.json()["job_id"]

    upload_response = client.post(
        f"/imports/jobs/{job_id}/files",
        headers=headers,
        json={
            "files": [
                {
                    "filename": "ubs.csv",
                    "content_base64": "Qm9va2luZyBEYXRlLFZhbHVlIERhdGUsVHJhbnNhY3Rpb24gRGV0YWlscyxEZWJpdCxDcmVkaXRcbjIwMjYtMDEtMDEsMjAyNi0wMS0wMSxQQUlFTUVOVCwxMC4wMCwwLjAw",
                }
            ]
        },
    )
    assert upload_response.status_code == 200
    assert captured_request["bank_account_id"] == "11111111-1111-1111-1111-111111111111"
    events = repo.events[UUID(job_id)]
    bank_detected_event = next(event for event in events if event.kind == "bank_detected")
    assert bank_detected_event.message == "Banque détectée : UBS\nCompte associé : UBS"
    bank_detected_index = next(index for index, event in enumerate(events) if event.kind == "bank_detected")
    started_index = next(index for index, event in enumerate(events) if event.kind == "started")
    assert bank_detected_index < started_index


def test_import_job_pipeline_uses_single_account_when_detected_bank_has_no_match(monkeypatch) -> None:
    auth_user_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(auth_user_id), "email": "user@example.com"},
    )

    class _ProfilesRepo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            return profile_id

        def list_bank_accounts(self, *, profile_id: UUID):
            return [{"id": "22222222-2222-2222-2222-222222222222", "name": "Revolut"}]

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID) -> dict[str, Any]:
            return {"state": {}}

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _ProfilesRepo())

    captured_request: dict[str, str] = {}

    class _BackendClient:
        def finance_releves_import_files(self, *, request: Any, on_progress: Any):
            captured_request["bank_account_id"] = str(request.bank_account_id)
            return {"imported_count": 1}

    class _Router:
        def __init__(self) -> None:
            self.backend_client = _BackendClient()

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    repo = _Repo()
    monkeypatch.setattr(agent_api, "_get_import_jobs_repository_or_501", lambda: repo)

    client = TestClient(app)
    headers = {"Authorization": "Bearer token"}

    create_response = client.post("/imports/jobs", headers=headers)
    job_id = create_response.json()["job_id"]

    client.post(
        f"/imports/jobs/{job_id}/files",
        headers=headers,
        json={
            "files": [
                {
                    "filename": "ubs.csv",
                    "content_base64": "Qm9va2luZyBEYXRlLFZhbHVlIERhdGUsVHJhbnNhY3Rpb24gRGV0YWlscyxEZWJpdCxDcmVkaXRcbjIwMjYtMDEtMDEsMjAyNi0wMS0wMSxQQUlFTUVOVCwxMC4wMCwwLjAw",
                }
            ]
        },
    )

    status_response = client.get(f"/imports/jobs/{job_id}", headers=headers)
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "done"
    assert captured_request["bank_account_id"] == "22222222-2222-2222-2222-222222222222"


def test_import_job_pipeline_uses_first_account_and_warns_when_detected_bank_is_ambiguous(monkeypatch) -> None:
    auth_user_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(auth_user_id), "email": "user@example.com"},
    )

    class _ProfilesRepo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            return profile_id

        def list_bank_accounts(self, *, profile_id: UUID):
            return [
                {"id": "11111111-1111-1111-1111-111111111111", "name": "UBS privé"},
                {"id": "33333333-3333-3333-3333-333333333333", "name": "UBS commun"},
            ]

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID) -> dict[str, Any]:
            return {"state": {}}

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _ProfilesRepo())

    captured_request: dict[str, str] = {}

    class _BackendClient:
        def finance_releves_import_files(self, *, request: Any, on_progress: Any):
            captured_request["bank_account_id"] = str(request.bank_account_id)
            return {"imported_count": 1}

    class _Router:
        def __init__(self) -> None:
            self.backend_client = _BackendClient()

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    repo = _Repo()
    monkeypatch.setattr(agent_api, "_get_import_jobs_repository_or_501", lambda: repo)

    client = TestClient(app)
    headers = {"Authorization": "Bearer token"}

    create_response = client.post("/imports/jobs", headers=headers)
    job_id = create_response.json()["job_id"]

    client.post(
        f"/imports/jobs/{job_id}/files",
        headers=headers,
        json={
            "files": [
                {
                    "filename": "ubs.csv",
                    "content_base64": "Qm9va2luZyBEYXRlLFZhbHVlIERhdGUsVHJhbnNhY3Rpb24gRGV0YWlscyxEZWJpdCxDcmVkaXRcbjIwMjYtMDEtMDEsMjAyNi0wMS0wMSxQQUlFTUVOVCwxMC4wMCwwLjAw",
                }
            ]
        },
    )

    status_response = client.get(f"/imports/jobs/{job_id}", headers=headers)
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "done"
    assert captured_request["bank_account_id"] == "11111111-1111-1111-1111-111111111111"
    warning_event = next(event for event in repo.events[UUID(job_id)] if event.kind == "warning")
    assert warning_event.message == (
        "Aucun compte bancaire n’a pu être détecté automatiquement. "
        "Le premier compte a été sélectionné par défaut."
    )
    assert warning_event.payload == {"bank_account_id": "11111111-1111-1111-1111-111111111111"}

def test_finalize_chat_then_yes_routes_to_report_and_not_import(monkeypatch) -> None:
    auth_user_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(auth_user_id), "email": "user@example.com"},
    )

    class _ProfilesRepo:
        def __init__(self) -> None:
            self.chat_state: dict[str, Any] = {
                "state": {
                    "global_state": {
                        "mode": "onboarding",
                        "onboarding_step": "import",
                        "onboarding_substep": "import_wait_ready",
                    }
                }
            }

        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id
            return profile_id

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID) -> dict[str, Any]:
            assert profile_id
            assert user_id
            return self.chat_state

        def update_chat_state(self, *, profile_id: UUID, user_id: UUID, chat_state: dict[str, Any]) -> None:
            assert profile_id
            assert user_id
            self.chat_state = chat_state

        def list_bank_accounts(self, *, profile_id: UUID):
            assert profile_id
            return []

    profiles_repo = _ProfilesRepo()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: profiles_repo)

    class _Router:
        def call(self, *_args, **_kwargs):
            return {"ok": True}

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    repo = _Repo()
    job_id = repo.create_job(profile_id=profile_id)
    repo.patch_job(profile_id=profile_id, job_id=job_id, payload={"status": "done", "result": {"imported_count": 4}})
    monkeypatch.setattr(agent_api, "_get_import_jobs_repository_or_501", lambda: repo)

    client = TestClient(app)
    headers = {"Authorization": "Bearer token"}

    finalize_response = client.post(f"/imports/jobs/{job_id}/finalize-chat", headers=headers)
    assert finalize_response.status_code == 200
    assert "Es-tu prêt à le découvrir ?" in finalize_response.json()["reply"]

    yes_response = client.post(
        "/agent/chat",
        headers=headers,
        json={"message": "Oui"},
    )
    assert yes_response.status_code == 200
    payload = yes_response.json()
    assert payload["reply"] == "Voici ton premier rapport financier !"
    assert payload["tool_result"]["type"] == "ui_request"
    assert payload["tool_result"]["name"] == "open_pdf_report"
    assert payload["tool_result"]["url"]


def test_finalize_import_job_chat_requires_done_status(monkeypatch) -> None:
    auth_user_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(auth_user_id), "email": "user@example.com"},
    )

    class _ProfilesRepo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id
            return profile_id

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _ProfilesRepo())

    repo = _Repo()
    job_id = repo.create_job(profile_id=profile_id)
    repo.patch_job(profile_id=profile_id, job_id=job_id, payload={"status": "running"})
    monkeypatch.setattr(agent_api, "_get_import_jobs_repository_or_501", lambda: repo)

    client = TestClient(app)
    headers = {"Authorization": "Bearer token"}

    response = client.post(f"/imports/jobs/{job_id}/finalize-chat", headers=headers)
    assert response.status_code == 409


def test_run_import_job_pipeline_autoselects_single_bank_account(monkeypatch) -> None:
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    job_id = uuid4()
    expected_account_id = "11111111-1111-1111-1111-111111111111"
    captured_request: dict[str, Any] = {}

    class _ProfilesRepo:
        def list_bank_accounts(self, *, profile_id: UUID) -> list[dict[str, Any]]:
            assert profile_id
            return [{"id": expected_account_id, "name": "UBS"}]

    class _BackendClient:
        def finance_releves_import_files(self, *, request: Any, on_progress: Any):
            captured_request["request"] = request
            assert request.bank_account_id == UUID(expected_account_id)
            on_progress("parsed_total", 1, 1)
            on_progress("categorization", 1, 1)
            return {"imported_count": 1}

    class _Router:
        def __init__(self) -> None:
            self.backend_client = _BackendClient()

    repo = _Repo()
    repo.jobs[job_id] = _Job(id=job_id, profile_id=profile_id, status="running")
    repo.events[job_id] = []

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _ProfilesRepo())
    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    payload = agent_api.ImportRequestPayload(
        files=[
            agent_api.ImportFilePayload(
                filename="sample.csv",
                content_base64="ZGF0ZSxtb250YW50XG4yMDI2LTAxLTAxLDEw",
            )
        ]
    )

    agent_api._run_import_job_pipeline(
        repository=repo,
        profile_id=profile_id,
        payload=payload,
        job_id=job_id,
    )

    assert "request" in captured_request
    assert captured_request["request"].bank_account_id == UUID(expected_account_id)
    assert repo.jobs[job_id].status == "done"
    assert repo.jobs[job_id].result is not None
    assert repo.jobs[job_id].result["bank_account_id"] == expected_account_id
