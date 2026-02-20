from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient

import agent.api as agent_api
from agent.api import app


client = TestClient(app)
AUTH_USER_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer token"}


def _mock_auth(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )


class _RepoWithCount:
    def __init__(self) -> None:
        self.count_values = [47, 27, 7, 0]

    def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
        assert auth_user_id == AUTH_USER_ID
        assert email == "user@example.com"
        return PROFILE_ID

    def count_map_alias_suggestions(self, *, profile_id: UUID) -> int:
        assert profile_id == PROFILE_ID
        return self.count_values.pop(0)


class _RepoWithoutCount:
    def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
        assert auth_user_id == AUTH_USER_ID
        assert email == "user@example.com"
        return PROFILE_ID


def test_resolve_pending_aliases_batches_until_count_reaches_zero(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    monkeypatch.setattr(agent_api._config, "llm_enabled", lambda: True)

    repo = _RepoWithCount()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)

    calls: list[int] = []
    resolver_results = [
        {
            "processed": 20,
            "applied": 15,
            "failed": 5,
            "created_entities": 2,
            "linked_aliases": 15,
            "updated_transactions": 30,
            "usage": {"input_tokens": 100, "output_tokens": 25},
            "warnings": ["w1"],
            "llm_run_id": "run-1",
        },
        {
            "processed": 20,
            "applied": 14,
            "failed": 6,
            "created_entities": 1,
            "linked_aliases": 14,
            "updated_transactions": 25,
            "usage": {"input_tokens": 90, "output_tokens": 20},
            "warnings": ["w2"],
            "llm_run_id": "run-2",
        },
        {
            "processed": 7,
            "applied": 7,
            "failed": 0,
            "created_entities": 0,
            "linked_aliases": 7,
            "updated_transactions": 12,
            "usage": {"input_tokens": 35, "output_tokens": 9},
            "warnings": ["w2"],
            "llm_run_id": "run-3",
        },
    ]

    def _resolve(**kwargs):
        assert kwargs["profile_id"] == PROFILE_ID
        calls.append(kwargs["limit"])
        return resolver_results.pop(0)

    monkeypatch.setattr(agent_api, "resolve_pending_map_alias", _resolve)

    response = client.post(
        "/finance/merchants/aliases/resolve-pending",
        headers=_headers(),
        json={"limit": 20, "max_batches": 10},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["type"] == "merchant_alias_resolve_result"
    assert payload["batches"] == 3
    assert payload["pending_before"] == 47
    assert payload["pending_after"] == 0
    assert calls == [20, 20, 20]
    assert payload["stats"]["processed"] == 47
    assert payload["stats"]["applied"] == 36
    assert payload["stats"]["failed"] == 11
    assert payload["stats"]["created_entities"] == 3
    assert payload["stats"]["linked_aliases"] == 36
    assert payload["stats"]["updated_transactions"] == 67
    assert payload["stats"]["usage"] == {"input_tokens": 225, "output_tokens": 54}
    assert payload["stats"]["warnings"] == ["w1", "w2"]
    assert payload["stats"]["llm_run_id"] == "run-3"


def test_resolve_pending_aliases_runs_single_batch_without_count_support(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    monkeypatch.setattr(agent_api._config, "llm_enabled", lambda: True)

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _RepoWithoutCount())

    calls: list[int] = []

    def _resolve(**kwargs):
        assert kwargs["profile_id"] == PROFILE_ID
        calls.append(kwargs["limit"])
        return {"processed": 5, "applied": 4, "failed": 1}

    monkeypatch.setattr(agent_api, "resolve_pending_map_alias", _resolve)

    response = client.post(
        "/finance/merchants/aliases/resolve-pending",
        headers=_headers(),
        json={"limit": 50, "max_batches": 10},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["batches"] == 1
    assert payload["pending_before"] is None
    assert payload["pending_after"] is None
    assert calls == [50]
    assert payload["stats"]["processed"] == 5
    assert payload["stats"]["applied"] == 4
    assert payload["stats"]["failed"] == 1


def test_pending_aliases_count_endpoint_returns_repository_count(monkeypatch) -> None:
    _mock_auth(monkeypatch)

    class _CountRepo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return PROFILE_ID

        def count_map_alias_suggestions(self, *, profile_id: UUID) -> int:
            assert profile_id == PROFILE_ID
            return 12

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _CountRepo())

    response = client.get("/finance/merchants/aliases/pending-count", headers=_headers())

    assert response.status_code == 200
    assert response.json() == {"pending_total_count": 12}


def test_pending_aliases_count_endpoint_returns_zero_when_repo_has_no_count_or_list(monkeypatch) -> None:
    _mock_auth(monkeypatch)

    class _RepoWithoutCountOrList:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return PROFILE_ID

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _RepoWithoutCountOrList())

    response = client.get("/finance/merchants/aliases/pending-count", headers=_headers())

    assert response.status_code == 200
    assert response.json() == {"pending_total_count": 0}
