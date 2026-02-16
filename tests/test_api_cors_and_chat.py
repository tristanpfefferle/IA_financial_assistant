"""Minimal API tests for CORS and resilient /agent/chat behavior."""

from __future__ import annotations

import importlib
from uuid import UUID

from fastapi.testclient import TestClient

import agent.api


def test_options_agent_chat_returns_cors_headers_for_ui_origin(monkeypatch) -> None:
    ui_origin = "https://ia-financial-assistant-ui.onrender.com"
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)
    monkeypatch.setenv("UI_ORIGIN", ui_origin)

    api = importlib.reload(agent.api)
    client = TestClient(api.app)

    response = client.options(
        "/agent/chat",
        headers={
            "Origin": ui_origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ui_origin


def test_agent_chat_returns_200_chat_response_when_profile_lookup_crashes(monkeypatch) -> None:
    api = importlib.reload(agent.api)
    client = TestClient(api.app)

    monkeypatch.setattr(
        api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")), "email": "user@example.com"},
    )

    class _Repo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            raise RuntimeError("db failure")

    monkeypatch.setattr(api, "get_profiles_repository", lambda: _Repo())

    response = client.post(
        "/agent/chat",
        json={"message": "ping"},
        headers={"Authorization": "Bearer token"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload.get("reply"), str)
    assert payload["reply"]
    assert payload["tool_result"] == {"error": "internal_server_error"}
    assert payload["plan"] is None
