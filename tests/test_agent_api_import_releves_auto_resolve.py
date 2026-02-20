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


class _Router:
    def call(self, tool_name: str, _payload: dict, *, profile_id: UUID | None = None):
        assert profile_id == PROFILE_ID
        assert tool_name == "finance_releves_import_files"
        return {"imported_count": 1}


class _BaseRepo:
    def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
        assert auth_user_id == AUTH_USER_ID
        assert email == "user@example.com"
        return PROFILE_ID

    def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
        assert profile_id == PROFILE_ID
        assert user_id == AUTH_USER_ID
        return {}

    def update_chat_state(self, *, profile_id: UUID, user_id: UUID, chat_state: dict):
        return None

    def list_releves_without_merchant(self, *, profile_id: UUID, limit: int = 500):
        assert profile_id == PROFILE_ID
        assert limit == 500
        return []

    def list_merchants(self, *, profile_id: UUID, limit: int = 5000):
        return []

    def create_merchant_suggestions(self, *, profile_id: UUID, suggestions: list[dict]):
        return len(suggestions)



def _mock_common(monkeypatch, repo: _BaseRepo) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())
    monkeypatch.setattr(
        agent_api,
        "run_merchant_cleanup",
        lambda **_kwargs: ([], "run", {}, {"raw_count": 0, "parsed_count": 0, "rejected_count": 0, "rejected_reasons": {}}),
    )



def test_import_releves_auto_resolve_runs_when_pending_within_limit(monkeypatch) -> None:
    class _Repo(_BaseRepo):
        def list_map_alias_suggestions(self, *, profile_id: UUID, limit: int = 100):
            assert profile_id == PROFILE_ID
            assert limit == 3
            return [{"id": "1"}, {"id": "2"}]

    repo = _Repo()
    _mock_common(monkeypatch, repo)
    monkeypatch.setattr(agent_api._config, "llm_enabled", lambda: True)
    monkeypatch.setattr(agent_api._config, "auto_resolve_merchant_aliases_enabled", lambda: True)
    monkeypatch.setattr(agent_api._config, "auto_resolve_merchant_aliases_limit", lambda: 2)

    resolver_calls: list[tuple[UUID, int]] = []

    def _resolve(**kwargs):
        resolver_calls.append((kwargs["profile_id"], kwargs["limit"]))
        return {"resolved": 2, "failed": 0}

    monkeypatch.setattr(agent_api, "resolve_pending_map_alias", _resolve)

    response = client.post(
        "/finance/releves/import",
        headers=_headers(),
        json={"files": [{"filename": "x.csv", "content_base64": "YQ=="}]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert resolver_calls == [(PROFILE_ID, 2)]
    assert payload["merchant_alias_auto_resolve"] == {
        "attempted": True,
        "skipped_reason": None,
        "stats": {"resolved": 2, "failed": 0},
    }



def test_import_releves_auto_resolve_skips_when_too_many_suggestions(monkeypatch) -> None:
    class _Repo(_BaseRepo):
        def list_map_alias_suggestions(self, *, profile_id: UUID, limit: int = 100):
            assert profile_id == PROFILE_ID
            assert limit == 3
            return [{"id": "1"}, {"id": "2"}, {"id": "3"}]

    repo = _Repo()
    _mock_common(monkeypatch, repo)
    monkeypatch.setattr(agent_api._config, "llm_enabled", lambda: True)
    monkeypatch.setattr(agent_api._config, "auto_resolve_merchant_aliases_enabled", lambda: True)
    monkeypatch.setattr(agent_api._config, "auto_resolve_merchant_aliases_limit", lambda: 2)

    resolver_call_count = {"count": 0}

    def _resolve(**_kwargs):
        resolver_call_count["count"] += 1
        return {}

    monkeypatch.setattr(agent_api, "resolve_pending_map_alias", _resolve)

    response = client.post(
        "/finance/releves/import",
        headers=_headers(),
        json={"files": [{"filename": "x.csv", "content_base64": "YQ=="}]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert resolver_call_count["count"] == 0
    assert payload["merchant_alias_auto_resolve"]["attempted"] is False
    assert payload["merchant_alias_auto_resolve"]["skipped_reason"] == "merchant_alias_auto_resolve_skipped_too_many"
    assert "merchant_alias_auto_resolve_skipped_too_many" in payload["warnings"]



def test_import_releves_auto_resolve_skips_when_llm_disabled(monkeypatch) -> None:
    class _Repo(_BaseRepo):
        def list_map_alias_suggestions(self, *, profile_id: UUID, limit: int = 100):
            raise AssertionError("should not be called")

    repo = _Repo()
    _mock_common(monkeypatch, repo)
    monkeypatch.setattr(agent_api._config, "llm_enabled", lambda: False)
    monkeypatch.setattr(agent_api._config, "auto_resolve_merchant_aliases_enabled", lambda: True)

    resolver_call_count = {"count": 0}

    def _resolve(**_kwargs):
        resolver_call_count["count"] += 1
        return {}

    monkeypatch.setattr(agent_api, "resolve_pending_map_alias", _resolve)

    response = client.post(
        "/finance/releves/import",
        headers=_headers(),
        json={"files": [{"filename": "x.csv", "content_base64": "YQ=="}]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert resolver_call_count["count"] == 0
    assert payload["merchant_alias_auto_resolve"]["attempted"] is False



def test_import_releves_auto_resolve_failure_keeps_import_success(monkeypatch) -> None:
    class _Repo(_BaseRepo):
        def list_map_alias_suggestions(self, *, profile_id: UUID, limit: int = 100):
            assert profile_id == PROFILE_ID
            assert limit == 3
            return [{"id": "1"}]

    repo = _Repo()
    _mock_common(monkeypatch, repo)
    monkeypatch.setattr(agent_api._config, "llm_enabled", lambda: True)
    monkeypatch.setattr(agent_api._config, "auto_resolve_merchant_aliases_enabled", lambda: True)
    monkeypatch.setattr(agent_api._config, "auto_resolve_merchant_aliases_limit", lambda: 2)

    def _resolve(**_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(agent_api, "resolve_pending_map_alias", _resolve)

    response = client.post(
        "/finance/releves/import",
        headers=_headers(),
        json={"files": [{"filename": "x.csv", "content_base64": "YQ=="}]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["merchant_alias_auto_resolve"] == {
        "attempted": True,
        "skipped_reason": None,
        "stats": None,
    }
    assert "merchant_alias_auto_resolve_failed" in payload["warnings"]
