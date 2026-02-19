"""Tests for global state bootstrap and persistence in chat_state.state."""

from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient

import agent.api as agent_api
from agent.api import app
from agent.loop import AgentReply


client = TestClient(app)
AUTH_USER_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


class _Repo:
    def __init__(
        self,
        *,
        initial_chat_state: dict[str, object] | None = None,
        profile_fields: dict[str, object] | None = None,
    ) -> None:
        self.chat_state = initial_chat_state or {}
        self.profile_fields = profile_fields or {}
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
        self.update_calls.append({"chat_state": dict(chat_state)})

    def get_profile_fields(self, *, profile_id: UUID, fields: list[str] | None = None) -> dict[str, object]:
        assert profile_id == PROFILE_ID
        assert fields == ["first_name", "last_name", "birth_date"]
        return dict(self.profile_fields)


class _LoopWithGlobal:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def handle_user_message(
        self,
        _message: str,
        *,
        profile_id: UUID | None = None,
        active_task=None,
        memory=None,
        global_state=None,
    ) -> AgentReply:
        self.calls.append(
            {
                "profile_id": profile_id,
                "active_task": active_task,
                "memory": memory,
                "global_state": global_state,
            }
        )
        return AgentReply(reply="ok")


class _LoopWithoutGlobal:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def handle_user_message(
        self,
        _message: str,
        *,
        profile_id: UUID | None = None,
        active_task=None,
        memory=None,
    ) -> AgentReply:
        self.calls.append(
            {
                "profile_id": profile_id,
                "active_task": active_task,
                "memory": memory,
            }
        )
        return AgentReply(reply="ok")


def _mock_auth(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )


def test_bootstrap_onboarding_if_profile_incomplete(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(initial_chat_state={"state": {}}, profile_fields={"first_name": None, "last_name": "X", "birth_date": None})
    loop = _LoopWithGlobal()

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "Bonjour"}, headers=_auth_headers())

    assert response.status_code == 200
    persisted_state = repo.update_calls[-1]["chat_state"]["state"]
    assert persisted_state["global_state"]["mode"] == "onboarding"
    assert persisted_state["global_state"]["onboarding_step"] == "profile"
    assert persisted_state["global_state"]["has_imported_transactions"] is False
    assert persisted_state["global_state"]["budget_created"] is False
    assert repo.update_calls[-1]["chat_state"].get("active_task") is None


def test_bootstrap_free_chat_if_profile_complete(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={"state": {}},
        profile_fields={"first_name": "Ada", "last_name": "Lovelace", "birth_date": "1815-12-10"},
    )
    loop = _LoopWithGlobal()

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "Bonjour"}, headers=_auth_headers())

    assert response.status_code == 200
    persisted_global_state = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted_global_state["mode"] == "free_chat"
    assert persisted_global_state["onboarding_step"] is None


def test_existing_global_state_is_not_overwritten(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    existing_global = {
        "mode": "guided_budget",
        "onboarding_step": "budget",
        "has_imported_transactions": True,
        "budget_created": False,
    }
    repo = _Repo(initial_chat_state={"state": {"global_state": dict(existing_global)}}, profile_fields={})
    loop = _LoopWithGlobal()

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "Salut"}, headers=_auth_headers())

    assert response.status_code == 200
    assert repo.update_calls == []
    assert loop.calls[-1]["global_state"] == existing_global


def test_does_not_pass_global_state_when_loop_handler_does_not_accept_it(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(initial_chat_state={"state": {}}, profile_fields={"first_name": None, "last_name": "X", "birth_date": None})
    loop = _LoopWithoutGlobal()

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "Bonjour"}, headers=_auth_headers())

    assert response.status_code == 200
    assert len(loop.calls) == 1


def test_reset_session_keeps_global_state(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "active_task": {"type": "clarification_pending"},
            "state": {
                "pending_clarification": {"field": "merchant"},
                "last_query": {"last_tool_name": "finance_releves_search"},
                "global_state": {"mode": "onboarding", "onboarding_step": "profile"},
            },
        },
    )

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)

    response = client.post("/agent/reset-session", headers=_auth_headers())

    assert response.status_code == 200
    saved_chat_state = repo.update_calls[-1]["chat_state"]
    assert saved_chat_state["active_task"] is None
    assert "pending_clarification" not in saved_chat_state["state"]
    assert saved_chat_state["state"]["last_query"] == {"last_tool_name": "finance_releves_search"}
    assert saved_chat_state["state"]["global_state"] == {"mode": "onboarding", "onboarding_step": "profile"}
