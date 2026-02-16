"""Tests for the FastAPI agent endpoints."""

from uuid import UUID

from fastapi.testclient import TestClient

import agent.api as agent_api
from agent.api import app
from agent.loop import AgentLoop


client = TestClient(app)
AUTH_USER_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def _mock_authenticated(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )

    class _Repo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    agent_api.get_profiles_repository.cache_clear()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())

class _DeleteRouter:
    def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
        assert tool_name == "finance_categories_delete"
        assert payload["category_name"] == "Transport"
        assert profile_id == UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        return None



def test_health_endpoint() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_agent_chat_requires_authorization_header() -> None:
    response = client.post("/agent/chat", json={"message": "ping"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing Authorization header"


def test_agent_chat_ping_pong(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    response = client.post("/agent/chat", json={"message": "ping"}, headers=_auth_headers())

    assert response.status_code == 200
    assert response.json() == {"reply": "pong", "tool_result": None, "plan": None}


def test_agent_chat_search_returns_tool_result(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    response = client.post("/agent/chat", json={"message": "search: coffee"}, headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload["reply"], str)
    assert payload["reply"]
    assert isinstance(payload["tool_result"], dict)
    assert isinstance(payload["plan"], dict)
    assert payload["plan"]["tool_name"] == "finance_releves_search"
    assert (
        "items" in payload["tool_result"]
        or {"code", "message"}.issubset(set(payload["tool_result"].keys()))
    )


def test_agent_chat_search_supports_date_range_filters(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    response = client.post(
        "/agent/chat",
        json={"message": "search: coffee from:2025-01-01 to:2025-01-31"},
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload["tool_result"], dict)
    assert "items" in payload["tool_result"]
    assert payload["tool_result"]["items"]
    assert all(("coffee" in (item.get("libelle") or "").lower()) or ("coffee" in (item.get("payee") or "").lower()) for item in payload["tool_result"]["items"])


def test_agent_chat_search_returns_validation_error_for_invalid_limit(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    response = client.post(
        "/agent/chat", json={"message": "search: coffee limit:0"}, headers=_auth_headers()
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["tool_result"]["code"] == "VALIDATION_ERROR"
    assert "details" in payload["tool_result"]


def test_agent_chat_search_returns_parse_validation_error(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    response = client.post(
        "/agent/chat", json={"message": "search: coffee from:2025-01-01"}, headers=_auth_headers()
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["tool_result"]["code"] == "VALIDATION_ERROR"
    assert "details" in payload["tool_result"]


def test_agent_chat_returns_unauthorized_when_auth_user_id_missing(monkeypatch) -> None:
    monkeypatch.setattr(agent_api, "get_user_from_bearer_token", lambda _token: {"email": "x@example.com"})

    response = client.post("/agent/chat", json={"message": "ping"}, headers=_auth_headers())

    assert response.status_code == 401
    assert response.json()["detail"] == "Unauthorized"


def test_agent_chat_profile_lookup_supports_fallback_email(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )

    class _Repo:
        def __init__(self) -> None:
            self.called = False

        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            self.called = True
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            # Simule le fallback interne par email (account_id non trouvé)
            return UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")

    repo = _Repo()
    agent_api.get_profiles_repository.cache_clear()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)

    response = client.post("/agent/chat", json={"message": "ping"}, headers=_auth_headers())

    assert repo.called is True
    assert response.status_code == 200


def test_agent_chat_returns_not_linked_message_when_profile_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )

    class _Repo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return None

    agent_api.get_profiles_repository.cache_clear()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())

    response = client.post("/agent/chat", json={"message": "ping"}, headers=_auth_headers())

    assert response.status_code == 401
    assert response.json()["detail"] == "No profile linked to authenticated user (by account_id or email)"


def test_agent_chat_delete_returns_json_reply_when_tool_returns_none(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: AgentLoop(tool_router=_DeleteRouter()))

    response = client.post(
        "/agent/chat",
        json={"message": 'Supprime la catégorie "Transport"'},
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()
    assert isinstance(payload["reply"], str)
    assert payload["reply"]
    assert payload["plan"]["tool_name"] == "finance_categories_delete"
    assert payload["tool_result"] == {"ok": True}
