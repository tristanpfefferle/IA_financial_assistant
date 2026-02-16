"""Tests for the FastAPI agent endpoints."""

from uuid import UUID

from fastapi.testclient import TestClient

import agent.api as agent_api
from agent.api import app


client = TestClient(app)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def _mock_authenticated(monkeypatch) -> None:
    monkeypatch.setattr(agent_api, "get_user_from_bearer_token", lambda _token: {"email": "user@example.com"})

    class _Repo:
        def get_profile_id_by_email(self, email: str):
            assert email == "user@example.com"
            return UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    agent_api.get_profiles_repository.cache_clear()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())


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
    assert payload["plan"]["tool_name"] == "finance_transactions_search"
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
    assert all("coffee" in item["description"].lower() for item in payload["tool_result"]["items"])


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
