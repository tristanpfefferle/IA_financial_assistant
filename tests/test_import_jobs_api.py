from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

import agent.api as agent_api
from agent.api import app


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
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id
            return profile_id

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _ProfilesRepo())

    class _BackendClient:
        def finance_releves_import_files(self, *, request: Any, on_progress: Any):
            assert request.files[0].filename == "sample.csv"
            on_progress("parsed_total", 47, 47)
            on_progress("categorization", 47, 47)
            return {"imported_count": 2}

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
    assert status_response.json()["status"] == "done"
    assert repo.jobs[UUID(job_id)].result is not None

    finalize_response = client.post(f"/imports/jobs/{job_id}/finalize-chat", headers=headers)
    assert finalize_response.status_code == 200
    payload = finalize_response.json()
    assert "Veux-tu afficher ton rapport mensuel maintenant ?" in payload["reply"]
    assert payload["tool_result"]["type"] == "ui_action"
    assert payload["tool_result"]["action"] == "quick_reply_yes_no"
    assert len(payload["tool_result"]["options"]) == 2

    events = repo.events[UUID(job_id)]
    kinds = [event.kind for event in events]
    assert "started" in kinds
    assert "parsing" in kinds
    assert "parsed" in kinds
    parsed_events = [event for event in events if event.kind == "parsed"]
    assert parsed_events
    assert parsed_events[0].message == "Transactions détectées : 47."
    assert "done" in kinds


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
