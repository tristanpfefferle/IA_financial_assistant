from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient

import agent.api as agent_api
from agent.api import app


client = TestClient(app)
AUTH_USER_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


class _Repo:
    def __init__(self) -> None:
        self.pending_values = [4, 2, 0]

    def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
        assert auth_user_id == AUTH_USER_ID
        assert email == "user@example.com"
        return PROFILE_ID

    def count_map_alias_suggestions(self, *, profile_id: UUID, include_failed: bool = False) -> int:
        assert profile_id == PROFILE_ID
        assert include_failed is False
        return self.pending_values.pop(0)


def test_resolve_map_alias_endpoint_returns_400_with_debug_when_background_disabled(monkeypatch) -> None:
    monkeypatch.setattr(agent_api._config, "auto_resolve_merchant_aliases_enabled", lambda: True)
    monkeypatch.setattr(agent_api._config, "llm_background_enabled", lambda: False)
    monkeypatch.setattr(agent_api._config, "llm_enabled", lambda: False)
    monkeypatch.setattr(agent_api._config, "llm_model", lambda: "gpt-test")
    monkeypatch.setattr(agent_api._config, "openai_api_key", lambda: "")

    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())

    response = client.post(
        "/finance/merchants/suggestions/resolve",
        headers=_auth_headers(),
        json={"limit": 10},
    )

    assert response.status_code == 400
    payload = response.json()
    assert "Background LLM resolver is disabled" in payload["detail"]["message"]
    assert payload["detail"]["debug"]["llm_background_enabled"] is False


def test_resolve_map_alias_endpoint_batches_until_zero(monkeypatch) -> None:
    monkeypatch.setattr(agent_api._config, "auto_resolve_merchant_aliases_enabled", lambda: True)
    monkeypatch.setattr(agent_api._config, "llm_background_enabled", lambda: True)
    monkeypatch.setattr(agent_api._config, "llm_enabled", lambda: True)
    monkeypatch.setattr(agent_api._config, "llm_model", lambda: "gpt-test")
    monkeypatch.setattr(agent_api._config, "openai_api_key", lambda: "key")

    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )

    repo = _Repo()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)

    calls: list[int] = []

    def _resolver(*, profile_id: UUID, profiles_repository, limit: int):
        calls.append(limit)
        assert profile_id == PROFILE_ID
        assert isinstance(profiles_repository, _Repo)
        return {
            "processed": limit,
            "applied": max(0, limit - 1),
            "created_entities": 1,
            "linked_aliases": max(0, limit - 1),
            "updated_transactions": limit,
            "failed": 1,
            "llm_run_id": f"run_{len(calls)}",
            "usage": {"total_tokens": 10},
            "warnings": [],
        }

    monkeypatch.setattr(agent_api, "resolve_pending_map_alias", _resolver)

    response = client.post(
        "/finance/merchants/suggestions/resolve",
        headers=_auth_headers(),
        json={"limit": 2, "max_per_run": 10},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["remaining_pending_count"] == 0
    assert payload["pending_total_count"] == 4
    assert payload["stats"]["processed"] == 4
    assert calls == [2, 2]
