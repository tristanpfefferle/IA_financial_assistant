"""Tests for global state bootstrap and persistence in chat_state.state."""

from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient
import pytest

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
        self.profile_update_calls: list[dict[str, object]] = []
        self.bank_accounts: list[dict[str, object]] = []
        self.ensure_bank_accounts_calls: list[dict[str, object]] = []

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

    def update_profile_fields(self, *, profile_id: UUID, set_dict: dict[str, object]) -> dict[str, object]:
        assert profile_id == PROFILE_ID
        self.profile_update_calls.append(dict(set_dict))
        self.profile_fields.update(set_dict)
        return dict(self.profile_fields)

    def list_bank_accounts(self, *, profile_id: UUID) -> list[dict[str, object]]:
        assert profile_id == PROFILE_ID
        return [dict(row) for row in self.bank_accounts]

    def ensure_bank_accounts(self, *, profile_id: UUID, names: list[str]) -> dict[str, object]:
        assert profile_id == PROFILE_ID
        normalized_names = [" ".join(name.strip().split()) for name in names if name.strip()]
        self.ensure_bank_accounts_calls.append({"names": list(normalized_names)})
        existing_lower = {str(row.get("name", "")).lower() for row in self.bank_accounts}
        created: list[str] = []
        existing: list[str] = []
        for name in normalized_names:
            lowered = name.lower()
            if lowered in existing_lower:
                existing.append(name)
                continue
            self.bank_accounts.append({"id": f"bank-{len(self.bank_accounts) + 1}", "name": name})
            existing_lower.add(lowered)
            created.append(name)
        return {"created": created, "existing": existing, "all": normalized_names}


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


class _LoopSpy:
    def __init__(self) -> None:
        self.called = False

    def handle_user_message(self, *_args, **_kwargs) -> AgentReply:
        self.called = True
        return AgentReply(reply="loop")


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


def test_bootstrap_onboarding_bank_accounts_if_profile_complete(monkeypatch) -> None:
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
    assert persisted_global_state["mode"] == "onboarding"
    assert persisted_global_state["onboarding_step"] == "bank_accounts"


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


def test_promotes_to_bank_accounts_onboarding_when_profile_becomes_complete(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "profile",
                    "has_imported_transactions": False,
                    "budget_created": False,
                }
            }
        },
        profile_fields={"first_name": "Ada", "last_name": "Lovelace", "birth_date": "1815-12-10"},
    )
    loop = _LoopWithGlobal()

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "Bonjour"}, headers=_auth_headers())

    assert response.status_code == 200
    assert repo.update_calls
    persisted_global_state = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted_global_state["mode"] == "onboarding"
    assert persisted_global_state["onboarding_step"] == "bank_accounts"
    assert persisted_global_state["has_imported_transactions"] is False
    assert persisted_global_state["budget_created"] is False


def test_does_not_pass_global_state_when_loop_handler_does_not_accept_it(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "guided_budget",
                    "onboarding_step": "budget",
                    "has_imported_transactions": False,
                    "budget_created": False,
                }
            }
        },
        profile_fields={"first_name": "Ada", "last_name": "X", "birth_date": "1815-12-10"},
    )
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


def test_onboarding_profile_name_message_updates_first_and_last_name(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {"global_state": {"mode": "onboarding", "onboarding_step": "profile"}}
        },
        profile_fields={"first_name": None, "last_name": None, "birth_date": None},
    )
    loop = _LoopSpy()

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "Tristan Pfefferlé"}, headers=_auth_headers())

    assert response.status_code == 200
    assert repo.profile_update_calls == [{"first_name": "Tristan", "last_name": "Pfefferlé"}]
    assert "Il me manque ta date de naissance" in response.json()["reply"]
    assert loop.called is False


@pytest.mark.parametrize(
    ("message", "expected_birth_date"),
    [
        ("1992-01-15", "1992-01-15"),
        ("12.01.2002", "2002-01-12"),
        ("14 janvier 2002", "2002-01-14"),
    ],
)
def test_onboarding_profile_birth_date_message_promotes_to_bank_accounts_step(
    monkeypatch, message: str, expected_birth_date: str
) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {"global_state": {"mode": "onboarding", "onboarding_step": "profile"}}
        },
        profile_fields={"first_name": "Tristan", "last_name": "Pfefferlé", "birth_date": None},
    )
    loop = _LoopSpy()

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": message}, headers=_auth_headers())

    assert response.status_code == 200
    assert repo.profile_update_calls == [{"birth_date": expected_birth_date}]
    assert repo.update_calls[-1]["chat_state"]["state"]["global_state"]["mode"] == "onboarding"
    assert repo.update_calls[-1]["chat_state"]["state"]["global_state"]["onboarding_step"] == "bank_accounts"
    assert "indique-moi tes banques / comptes" in response.json()["reply"]
    assert loop.called is False


def test_onboarding_profile_non_profile_message_returns_help_and_skips_loop(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {"global_state": {"mode": "onboarding", "onboarding_step": "profile"}}
        },
        profile_fields={"first_name": None, "last_name": None, "birth_date": None},
    )
    loop = _LoopSpy()

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "Liste mes catégories"}, headers=_auth_headers())

    assert response.status_code == 200
    assert repo.profile_update_calls == []
    assert "Pour démarrer" in response.json()["reply"]
    assert loop.called is False


def test_onboarding_profile_complete_routes_to_bank_accounts_step(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {"global_state": {"mode": "onboarding", "onboarding_step": "profile"}}
        },
        profile_fields={"first_name": "Tristan", "last_name": "Pfefferlé", "birth_date": "1992-01-15"},
    )
    loop = _LoopSpy()

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "Liste mes catégories"}, headers=_auth_headers())

    assert response.status_code == 200
    assert "UBS, Revolut" in response.json()["reply"]
    assert loop.called is False


def test_onboarding_bank_accounts_help_when_none_exist_and_no_names_provided(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {"global_state": {"mode": "onboarding", "onboarding_step": "bank_accounts"}}
        },
        profile_fields={"first_name": "Tristan", "last_name": "Pfefferlé", "birth_date": "1992-01-15"},
    )
    loop = _LoopSpy()

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "Salut"}, headers=_auth_headers())

    assert response.status_code == 200
    assert "UBS, Revolut" in response.json()["reply"]
    assert repo.ensure_bank_accounts_calls == []
    assert loop.called is False


def test_onboarding_bank_accounts_creates_accounts_and_moves_to_import(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {"global_state": {"mode": "onboarding", "onboarding_step": "bank_accounts"}}
        },
        profile_fields={"first_name": "Tristan", "last_name": "Pfefferlé", "birth_date": "1992-01-15"},
    )
    loop = _LoopSpy()

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "UBS et Revolut"}, headers=_auth_headers())

    assert response.status_code == 200
    assert repo.ensure_bank_accounts_calls == [{"names": ["UBS", "Revolut"]}]
    persisted_global_state = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted_global_state["onboarding_step"] == "import"
    assert persisted_global_state["has_bank_accounts"] is True
    assert "Comptes créés: UBS, Revolut" in response.json()["reply"]
    assert loop.called is False


def test_onboarding_bank_accounts_skips_creation_if_already_exists(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {"global_state": {"mode": "onboarding", "onboarding_step": "bank_accounts"}}
        },
        profile_fields={"first_name": "Tristan", "last_name": "Pfefferlé", "birth_date": "1992-01-15"},
    )
    repo.bank_accounts = [{"id": "existing-1", "name": "UBS"}]
    loop = _LoopSpy()

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "n'importe"}, headers=_auth_headers())

    assert response.status_code == 200
    assert repo.ensure_bank_accounts_calls == []
    persisted_global_state = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted_global_state["onboarding_step"] == "import"
    assert persisted_global_state["has_bank_accounts"] is True
    assert "j’ai déjà tes comptes" in response.json()["reply"]
    assert loop.called is False
