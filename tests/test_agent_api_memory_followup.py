"""End-to-end API test for persisted query memory follow-ups."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi.testclient import TestClient

import agent.api as agent_api
from agent.api import app
from agent.loop import AgentLoop


client = TestClient(app)
AUTH_USER_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def _as_iso_date(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    assert isinstance(value, str)
    return value


class _Repo:
    def __init__(self, initial_chat_state: dict[str, object] | None = None) -> None:
        self.chat_state = initial_chat_state or {}
        self.update_calls: list[dict[str, object]] = []

    def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
        assert auth_user_id == AUTH_USER_ID
        assert email == "user@example.com"
        return PROFILE_ID

    def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
        assert profile_id == PROFILE_ID
        assert user_id == AUTH_USER_ID
        return dict(self.chat_state)

    def update_chat_state(self, *, profile_id: UUID, user_id: UUID, chat_state: dict[str, object]) -> None:
        assert profile_id == PROFILE_ID
        assert user_id == AUTH_USER_ID
        self.chat_state = dict(chat_state)
        self.update_calls.append({"profile_id": profile_id, "user_id": user_id, "chat_state": dict(chat_state)})


class _Router:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call(self, tool_name: str, payload: dict[str, object], *, profile_id: UUID | None = None):
        assert profile_id == PROFILE_ID
        self.calls.append((tool_name, dict(payload)))
        if tool_name == "finance_releves_search":
            return {"ok": True, "items": []}
        if tool_name == "finance_releves_sum":
            return {"ok": True, "total": 123.45}
        if tool_name == "finance_categories_list":
            return {"categories": [{"id": "1", "name": "Alimentation"}]}
        raise AssertionError(f"unexpected tool call: {tool_name}")


def test_agent_chat_does_not_guess_category_followup_without_known_categories(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )

    repo = _Repo(initial_chat_state={})
    router = _Router()
    loop = AgentLoop(tool_router=router)

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    first = client.post(
        "/agent/chat",
        json={"message": "Total dépenses en janvier 2026"},
        headers=_auth_headers(),
    )

    assert first.status_code == 200
    assert router.calls[0][0] == "finance_releves_sum"
    assert router.calls[0][1]["direction"] == "DEBIT_ONLY"
    assert _as_iso_date(router.calls[0][1]["date_range"]["start_date"]) == "2026-01-01"
    assert _as_iso_date(router.calls[0][1]["date_range"]["end_date"]) == "2026-01-31"
    assert isinstance(repo.chat_state.get("state"), dict)
    assert isinstance(repo.chat_state["state"].get("last_query"), dict)
    assert repo.chat_state["state"]["last_query"]["date_range"] == {
        "start_date": "2026-01-01",
        "end_date": "2026-01-31",
    }
    assert repo.chat_state["state"]["last_query"]["filters"] == {
        "direction": "DEBIT_ONLY"
    }

    second = client.post(
        "/agent/chat",
        json={"message": "Ok et en logement ?"},
        headers=_auth_headers(),
    )

    assert second.status_code == 200
    assert second.json()["plan"] is None
    assert second.json()["tool_result"] is None
    assert len(router.calls) == 1


def test_agent_chat_list_categories_is_not_hijacked_by_followup_memory(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )

    repo = _Repo(initial_chat_state={})
    router = _Router()
    loop = AgentLoop(tool_router=router)

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    first = client.post(
        "/agent/chat",
        json={"message": "Total dépenses en janvier 2026"},
        headers=_auth_headers(),
    )
    assert first.status_code == 200
    assert router.calls[0][0] == "finance_releves_sum"

    second = client.post(
        "/agent/chat",
        json={"message": "Liste mes catégories"},
        headers=_auth_headers(),
    )

    assert second.status_code == 200
    assert second.json()["plan"]["tool_name"] == "finance_categories_list"
    assert router.calls[1] == ("finance_categories_list", {})


def test_agent_chat_exposes_debug_memory_injected_when_memory_adds_period(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )

    repo = _Repo(
        initial_chat_state={
            "state": {
                "last_query": {
                    "date_range": {"start_date": "2026-01-01", "end_date": "2026-01-31"},
                    "last_tool_name": "finance_releves_sum",
                    "last_intent": "sum",
                    "filters": {"direction": "DEBIT_ONLY"},
                }
            }
        }
    )
    router = _Router()
    loop = AgentLoop(tool_router=router)

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post(
        "/agent/chat",
        json={"message": "search: coop"},
        headers={**_auth_headers(), "X-Debug": "1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["plan"]["tool_name"] == "finance_releves_search"
    assert body["plan"]["meta"]["debug_memory_injected"] == {
        "date_range": {"start_date": "2026-01-01", "end_date": "2026-01-31"},
        "direction": "DEBIT_ONLY",
    }
    assert body["plan"]["meta"]["debug_followup_used"] is False
    assert body["plan"]["meta"]["debug_query_memory_used"]["date_range"] == {
        "start_date": "2026-01-01",
        "end_date": "2026-01-31",
    }
