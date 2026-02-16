"""Tests for active_task persistence in agent API workflow."""

from __future__ import annotations

from types import SimpleNamespace
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



def test_agent_chat_sets_active_task_on_delete_confirmation_request(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )

    repo = _Repo(initial_chat_state={})

    class _Loop:
        def handle_user_message(self, message: str, *, profile_id: UUID | None = None, active_task=None):
            assert message == "Supprime la catégorie X"
            assert profile_id == PROFILE_ID
            assert active_task is None
            return SimpleNamespace(
                reply="Répondez OUI ou NON pour confirmer.",
                tool_result=None,
                plan=None,
                should_update_active_task=True,
                active_task={"type": "confirm_delete_category", "category_name": "X"},
            )

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _Loop())

    response = client.post("/agent/chat", json={"message": "Supprime la catégorie X"}, headers=_auth_headers())

    assert response.status_code == 200
    assert "confirmer" in response.json()["reply"]
    assert repo.update_calls
    assert repo.update_calls[-1]["user_id"] == AUTH_USER_ID
    assert repo.update_calls[-1]["chat_state"]["active_task"] == {
        "type": "confirm_delete_category",
        "category_name": "X",
    }



def test_agent_chat_uses_persisted_active_task_and_clears_after_confirmation(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )

    repo = _Repo(
        initial_chat_state={
            "active_task": {"type": "confirm_delete_category", "category_name": "X"}
        }
    )

    class _Loop:
        def handle_user_message(self, message: str, *, profile_id: UUID | None = None, active_task=None):
            assert message == "oui"
            assert profile_id == PROFILE_ID
            assert active_task == {"type": "confirm_delete_category", "category_name": "X"}
            return SimpleNamespace(
                reply="Catégorie supprimée.",
                tool_result={"ok": True},
                plan={"tool_name": "finance_categories_delete", "payload": {"category_name": "X"}},
                should_update_active_task=True,
                active_task=None,
            )

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _Loop())

    response = client.post("/agent/chat", json={"message": "oui"}, headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()["plan"]["tool_name"] == "finance_categories_delete"
    assert repo.update_calls
    assert repo.update_calls[-1]["user_id"] == AUTH_USER_ID
    assert "active_task" not in repo.update_calls[-1]["chat_state"]
