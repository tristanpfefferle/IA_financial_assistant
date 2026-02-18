"""Tests for active_task persistence in agent API workflow."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

import agent.api as agent_api
import agent.loop as loop_module
from agent.api import app
from agent.loop import AgentLoop


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
        def handle_user_message(self, message: str, *, profile_id: UUID | None = None, active_task=None, memory=None):
            assert message == "Supprime la catégorie X"
            assert profile_id == PROFILE_ID
            assert active_task is None
            assert memory is None
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
        def handle_user_message(self, message: str, *, profile_id: UUID | None = None, active_task=None, memory=None):
            assert message == "oui"
            assert profile_id == PROFILE_ID
            assert active_task == {"type": "confirm_delete_category", "category_name": "X"}
            assert memory is None
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


def test_agent_chat_reuses_persisted_search_active_task_with_serialized_dates(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )

    repo = _Repo(initial_chat_state={})

    class _SearchRouter:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        def call(self, tool_name: str, payload: dict[str, object], *, profile_id: UUID | None = None):
            self.calls.append((tool_name, payload))
            assert profile_id == PROFILE_ID
            assert tool_name == "finance_releves_search"
            return {"ok": True, "items": []}

    router = _SearchRouter()
    loop = AgentLoop(tool_router=router)

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    first = client.post("/agent/chat", json={"message": "recherche en janvier 2026"}, headers=_auth_headers())

    assert first.status_code == 200
    assert first.json()["tool_result"] == {
        "type": "clarification",
        "clarification_type": "awaiting_search_merchant",
        "message": "Que voulez-vous rechercher (ex: Migros, coffee, Coop) ?",
        "payload": {"date_range": {"start_date": "2026-01-01", "end_date": "2026-01-31"}},
    }
    assert repo.chat_state == {
        "active_task": {
            "type": "awaiting_search_merchant",
            "date_range": {"start_date": "2026-01-01", "end_date": "2026-01-31"},
        }
    }

    second = client.post("/agent/chat", json={"message": "Coop"}, headers=_auth_headers())

    assert second.status_code == 200
    assert second.json()["plan"] == {
        "tool_name": "finance_releves_search",
        "payload": {
            "merchant": "coop",
            "limit": 50,
            "offset": 0,
            "date_range": {"start_date": "2026-01-01", "end_date": "2026-01-31"},
        },
    }
    assert router.calls == [
        (
            "finance_releves_search",
            {
                "merchant": "coop",
                "limit": 50,
                "offset": 0,
                "date_range": {"start_date": "2026-01-01", "end_date": "2026-01-31"},
            },
        )
    ]
    assert isinstance(repo.chat_state.get("state"), dict)
    assert isinstance(repo.chat_state["state"].get("last_query"), dict)
    assert repo.chat_state["state"]["last_query"]["date_range"] == {
        "start_date": "2026-01-01",
        "end_date": "2026-01-31",
    }
    assert repo.chat_state["state"]["last_query"]["filters"] == {"merchant": "coop"}


def test_agent_chat_persists_memory_update(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )

    repo = _Repo(initial_chat_state={"active_task": {"type": "awaiting_search_merchant"}})

    class _Loop:
        def handle_user_message(
            self,
            message: str,
            *,
            profile_id: UUID | None = None,
            active_task=None,
            memory=None,
        ):
            assert message == "Total dépenses"
            assert profile_id == PROFILE_ID
            assert active_task == {"type": "awaiting_search_merchant"}
            assert memory is None
            return SimpleNamespace(
                reply="Montant total: 100",
                tool_result={"ok": True, "total": 100},
                plan={"tool_name": "finance_releves_sum", "payload": {"direction": "DEBIT_ONLY"}},
                should_update_active_task=False,
                active_task=None,
                memory_update={
                    "last_query": {
                        "date_range": {
                            "start_date": "2026-01-01",
                            "end_date": "2026-01-31",
                        },
                        "filters": {"direction": "DEBIT_ONLY"},
                    }
                },
            )

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _Loop())

    response = client.post("/agent/chat", json={"message": "Total dépenses"}, headers=_auth_headers())

    assert response.status_code == 200
    assert repo.update_calls
    assert repo.chat_state == {
        "active_task": {"type": "awaiting_search_merchant"},
        "state": {
            "last_query": {
                "date_range": {
                    "start_date": "2026-01-01",
                    "end_date": "2026-01-31",
                },
                "filters": {"direction": "DEBIT_ONLY"},
            }
        },
    }


def test_agent_chat_clarification_pending_uses_new_period_across_requests(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )

    def _parse_intent(message: str):
        if message == "Dépenses en Loisir en décembre 2025":
            return {
                "type": "tool_call",
                "tool_name": "finance_releves_sum",
                "payload": {
                    "direction": "DEBIT_ONLY",
                    "categorie": "Loisir",
                    "date_range": {
                        "start_date": "2025-12-01",
                        "end_date": "2025-12-31",
                    },
                },
            }
        if message == "Et en janvier 2026 ?":
            return {
                "type": "clarification",
                "message": "Tu veux les dépenses, revenus ou le solde ?",
                "clarification_type": "missing_direction",
            }
        return {"type": "noop"}

    monkeypatch.setattr(loop_module, "parse_intent", _parse_intent)

    repo = _Repo(initial_chat_state={"state": {"known_categories": ["Loisir"]}})

    class _Router:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        def call(self, tool_name: str, payload: dict[str, object], *, profile_id: UUID | None = None):
            assert profile_id == PROFILE_ID
            self.calls.append((tool_name, dict(payload)))
            assert tool_name == "finance_releves_sum"
            return {"ok": True, "total": 10.0}

    router = _Router()
    loop = AgentLoop(tool_router=router)

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    first = client.post(
        "/agent/chat",
        json={"message": "Dépenses en Loisir en décembre 2025"},
        headers=_auth_headers(),
    )
    assert first.status_code == 200
    assert first.json()["plan"] == {
        "tool_name": "finance_releves_sum",
        "payload": {
            "direction": "DEBIT_ONLY",
            "categorie": "Loisir",
            "date_range": {
                "start_date": "2025-12-01",
                "end_date": "2025-12-31",
            },
        },
    }

    second = client.post(
        "/agent/chat",
        json={"message": "Et en janvier 2026 ?"},
        headers={**_auth_headers(), "X-Debug": "1"},
    )
    assert second.status_code == 200
    second_plan = second.json()["plan"]
    assert second_plan["tool_name"] == "finance_releves_sum"
    assert second_plan["payload"] == {
        "direction": "DEBIT_ONLY",
        "date_range": {
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
        },
        "categorie": "Loisir",
    }
    assert second_plan["meta"]["debug_source"] == "followup"
    assert second_plan["meta"]["debug_period_detected"] == {
        "month": 1,
        "year": 2026,
        "date_range": {
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
        },
    }
    assert len(router.calls) == 2
    assert router.calls[1] == (
        "finance_releves_sum",
        {
            "direction": "DEBIT_ONLY",
            "date_range": {
                "start_date": "2026-01-01",
                "end_date": "2026-01-31",
            },
            "categorie": "Loisir",
        },
    )
    assert router.calls[1][1]["date_range"] != {
        "start_date": "2025-12-01",
        "end_date": "2025-12-31",
    }
    assert "active_task" not in repo.chat_state


def test_agent_chat_with_debug_header_works_when_loop_does_not_accept_debug(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )

    repo = _Repo(initial_chat_state={"active_task": {"type": "awaiting_search_merchant"}})

    class _LegacyLoop:
        def handle_user_message(self, message: str, *, profile_id: UUID | None = None, active_task=None, memory=None):
            assert message == "Montre mes dépenses"
            assert profile_id == PROFILE_ID
            assert active_task == {"type": "awaiting_search_merchant"}
            assert memory is None
            return SimpleNamespace(
                reply="Voici vos dépenses.",
                tool_result={"ok": True},
                plan={"tool_name": "finance_releves_sum", "payload": {"direction": "DEBIT_ONLY"}},
                should_update_active_task=False,
                active_task=None,
            )

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LegacyLoop())

    response = client.post(
        "/agent/chat",
        json={"message": "Montre mes dépenses"},
        headers={**_auth_headers(), "X-Debug": "1"},
    )

    assert response.status_code == 200
    assert response.json()["reply"] == "Voici vos dépenses."
    assert response.json()["plan"] == {
        "tool_name": "finance_releves_sum",
        "payload": {"direction": "DEBIT_ONLY"},
    }
