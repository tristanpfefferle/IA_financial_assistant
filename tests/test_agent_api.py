"""Tests for the FastAPI agent endpoints."""

from fastapi.testclient import TestClient

from agent.api import app


client = TestClient(app)


def test_health_endpoint() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_agent_chat_ping_pong() -> None:
    response = client.post("/agent/chat", json={"message": "ping"})

    assert response.status_code == 200
    assert response.json() == {"reply": "pong", "tool_result": None}


def test_agent_chat_search_returns_tool_result() -> None:
    response = client.post("/agent/chat", json={"message": "search: coffee"})

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload["reply"], str)
    assert payload["reply"]
    assert isinstance(payload["tool_result"], dict)
    assert (
        "items" in payload["tool_result"]
        or {"code", "message"}.issubset(set(payload["tool_result"].keys()))
    )
