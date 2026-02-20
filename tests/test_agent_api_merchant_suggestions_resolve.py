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
    def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
        assert auth_user_id == AUTH_USER_ID
        assert email == "user@example.com"
        return PROFILE_ID


def test_resolve_map_alias_endpoint_returns_stats(monkeypatch) -> None:
    monkeypatch.setattr(agent_api._config, "llm_enabled", lambda: True)
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())

    call_order: list[str] = []

    def _resolver(*, profile_id: UUID, profiles_repository, limit: int):
        call_order.extend(["resolver_called"])
        assert profile_id == PROFILE_ID
        assert isinstance(profiles_repository, _Repo)
        assert limit == 77
        return {
            "processed": 2,
            "applied": 1,
            "created_entities": 1,
            "linked_aliases": 1,
            "updated_transactions": 3,
            "failed": 1,
            "llm_run_id": "run_abc",
            "usage": {"total_tokens": 42},
            "warnings": [],
        }

    monkeypatch.setattr(agent_api, "resolve_pending_map_alias", _resolver)

    response = client.post(
        "/finance/merchants/suggestions/resolve",
        headers=_auth_headers(),
        json={"limit": 77},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["processed"] == 2
    assert payload["llm_run_id"] == "run_abc"
    assert call_order == ["resolver_called"]


def test_resolve_map_alias_endpoint_fails_when_llm_disabled(monkeypatch) -> None:
    monkeypatch.setattr(agent_api._config, "llm_enabled", lambda: False)

    response = client.post(
        "/finance/merchants/suggestions/resolve",
        headers=_auth_headers(),
        json={"limit": 10},
    )

    assert response.status_code == 400
    assert "LLM is disabled" in response.json()["detail"]
