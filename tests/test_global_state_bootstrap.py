"""Tests for onboarding global state sequencing and persistence."""

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
        self.bank_accounts: list[dict[str, object]] = []
        self.update_calls: list[dict[str, object]] = []
        self.profile_update_calls: list[dict[str, object]] = []
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
        self.profile_fields.update(set_dict)
        self.profile_update_calls.append(dict(set_dict))
        return dict(self.profile_fields)

    def list_bank_accounts(self, *, profile_id: UUID) -> list[dict[str, object]]:
        assert profile_id == PROFILE_ID
        return [dict(row) for row in self.bank_accounts]

    def ensure_bank_accounts(self, *, profile_id: UUID, names: list[str]) -> dict[str, object]:
        assert profile_id == PROFILE_ID
        self.ensure_bank_accounts_calls.append({"names": list(names)})
        existing_lower = {str(row.get("name", "")).lower() for row in self.bank_accounts}
        created: list[str] = []
        for name in names:
            lowered = name.lower()
            if lowered in existing_lower:
                continue
            self.bank_accounts.append({"id": f"bank-{len(self.bank_accounts)+1}", "name": name})
            existing_lower.add(lowered)
            created.append(name)
        return {"created": created, "all": names}


class _LoopSpy:
    def __init__(self) -> None:
        self.called = False

    def handle_user_message(self, *_args, **_kwargs) -> AgentReply:
        self.called = True
        return AgentReply(reply="loop")


class _LoopWithGlobal:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def handle_user_message(self, _message: str, *, profile_id=None, active_task=None, memory=None, global_state=None) -> AgentReply:
        self.calls.append({"profile_id": profile_id, "active_task": active_task, "memory": memory, "global_state": global_state})
        return AgentReply(reply="ok")


def _mock_auth(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )


def test_bootstrap_profile_complete_routes_to_profile_confirm(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={"state": {}},
        profile_fields={"first_name": "Ada", "last_name": "Lovelace", "birth_date": "1815-12-10"},
    )
    loop = _LoopSpy()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "Bonjour"}, headers=_auth_headers())

    assert response.status_code == 200
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_step"] == "profile"
    assert persisted["onboarding_substep"] == "profile_confirm"
    assert loop.called is False


def test_profile_confirm_yes_moves_to_bank_accounts_collect(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "profile",
                    "onboarding_substep": "profile_confirm",
                    "profile_confirmed": False,
                    "bank_accounts_confirmed": False,
                }
            }
        },
        profile_fields={"first_name": "Ada", "last_name": "Lovelace", "birth_date": "1815-12-10"},
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "oui"}, headers=_auth_headers())

    assert response.status_code == 200
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_step"] == "bank_accounts"
    assert persisted["onboarding_substep"] == "bank_accounts_collect"
    assert persisted["profile_confirmed"] is True


def test_profile_confirm_no_moves_back_to_collect(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "profile",
                    "onboarding_substep": "profile_confirm",
                }
            }
        },
        profile_fields={"first_name": "Ada", "last_name": "Lovelace", "birth_date": "1815-12-10"},
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "non"}, headers=_auth_headers())

    assert response.status_code == 200
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_substep"] == "profile_collect"


def test_bank_accounts_collect_creates_then_moves_to_confirm(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "bank_accounts",
                    "onboarding_substep": "bank_accounts_collect",
                }
            }
        },
        profile_fields={"first_name": "Ada", "last_name": "Lovelace", "birth_date": "1815-12-10"},
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "UBS et Revolut"}, headers=_auth_headers())

    assert response.status_code == 200
    assert repo.ensure_bank_accounts_calls == [{"names": ["UBS", "Revolut"]}]
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_substep"] == "bank_accounts_confirm"


def test_bank_accounts_confirm_yes_back_to_collect(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "bank_accounts",
                    "onboarding_substep": "bank_accounts_confirm",
                }
            }
        },
        profile_fields={"first_name": "Ada", "last_name": "Lovelace", "birth_date": "1815-12-10"},
    )
    repo.bank_accounts = [{"id": "bank-1", "name": "UBS"}]
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "oui"}, headers=_auth_headers())

    assert response.status_code == 200
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_substep"] == "bank_accounts_collect"


def test_bank_accounts_confirm_no_moves_to_import_select(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "bank_accounts",
                    "onboarding_substep": "bank_accounts_confirm",
                }
            }
        },
        profile_fields={"first_name": "Ada", "last_name": "Lovelace", "birth_date": "1815-12-10"},
    )
    repo.bank_accounts = [{"id": "bank-1", "name": "UBS"}]
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "non"}, headers=_auth_headers())

    assert response.status_code == 200
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_step"] == "import"
    assert persisted["onboarding_substep"] == "import_select_account"
    assert persisted["bank_accounts_confirmed"] is True


def test_import_select_account_ubs_selects_account_and_skips_loop(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "import",
                    "onboarding_substep": "import_select_account",
                }
            }
        },
        profile_fields={"first_name": "Ada", "last_name": "Lovelace", "birth_date": "1815-12-10"},
    )
    repo.bank_accounts = [{"id": "bank-1", "name": "UBS"}]
    loop = _LoopSpy()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "UBS"}, headers=_auth_headers())

    assert response.status_code == 200
    persisted_state = repo.update_calls[-1]["chat_state"]["state"]
    assert persisted_state["import_context"]["selected_bank_account_id"] == "bank-1"
    assert persisted_state["import_context"]["selected_bank_account_name"] == "UBS"
    assert loop.called is False


def test_onboarding_reply_from_loop_adds_short_reminder(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "categories",
                    "onboarding_substep": "import_select_account",
                    "has_bank_accounts": True,
                }
            }
        },
        profile_fields={"first_name": "Ada", "last_name": "Lovelace", "birth_date": "1815-12-10"},
    )
    loop = _LoopWithGlobal()
    repo.bank_accounts = [{"id": "bank-1", "name": "UBS"}]
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "liste mes catégories"}, headers=_auth_headers())

    assert response.status_code == 200
    assert "(Pour continuer : indique le compte à importer.)" in response.json()["reply"]


class _RepoWithoutListBankAccounts(_Repo):
    def __getattribute__(self, name: str):
        if name == "list_bank_accounts":
            raise AttributeError(name)
        return super().__getattribute__(name)


class _RepoListBankAccountsRaises(_Repo):
    def list_bank_accounts(self, *, profile_id: UUID) -> list[dict[str, object]]:
        raise RuntimeError("db unavailable")


class _LoopWithMemoryUpdate:
    def __init__(self, memory_update: dict[str, object]) -> None:
        self.memory_update = memory_update

    def handle_user_message(self, *_args, **_kwargs) -> AgentReply:
        return AgentReply(reply="ok", memory_update=self.memory_update)


def test_bootstrap_profile_incomplete_routes_to_profile_collect(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(initial_chat_state={"state": {}}, profile_fields={"first_name": None, "last_name": "X", "birth_date": None})
    loop = _LoopSpy()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "Bonjour"}, headers=_auth_headers())

    assert response.status_code == 200
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_step"] == "profile"
    assert persisted["onboarding_substep"] == "profile_collect"
    assert loop.called is False


def test_existing_global_state_is_not_overwritten(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    existing_global = {
        "mode": "guided_budget",
        "onboarding_step": "budget",
        "onboarding_substep": None,
        "has_imported_transactions": True,
        "budget_created": False,
    }
    repo = _Repo(initial_chat_state={"state": {"global_state": dict(existing_global)}})
    loop = _LoopWithGlobal()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "Salut"}, headers=_auth_headers())

    assert response.status_code == 200
    assert repo.update_calls == []
    assert loop.calls[-1]["global_state"] == existing_global


def test_reset_session_keeps_global_state(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "active_task": {"type": "clarification_pending"},
            "state": {
                "pending_clarification": {"field": "merchant"},
                "last_query": {"last_tool_name": "finance_releves_search"},
                "global_state": {"mode": "onboarding", "onboarding_step": "profile", "onboarding_substep": "profile_collect"},
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
    assert saved_chat_state["state"]["global_state"]["onboarding_step"] == "profile"


def test_free_chat_does_not_re_gate_when_list_bank_accounts_is_unavailable(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _RepoWithoutListBankAccounts(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "free_chat",
                    "onboarding_step": None,
                    "onboarding_substep": None,
                    "has_bank_accounts": True,
                    "has_imported_transactions": False,
                    "budget_created": False,
                }
            }
        },
    )
    loop = _LoopSpy()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "Salut"}, headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()["reply"] == "loop"
    assert repo.update_calls == []
    assert loop.called is True


def test_free_chat_does_not_re_gate_when_list_bank_accounts_raises(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _RepoListBankAccountsRaises(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "free_chat",
                    "onboarding_step": None,
                    "onboarding_substep": None,
                    "has_bank_accounts": True,
                    "has_imported_transactions": False,
                    "budget_created": False,
                }
            }
        },
    )
    loop = _LoopSpy()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "Salut"}, headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()["reply"] == "loop"
    assert repo.update_calls == []
    assert loop.called is True


def test_onboarding_reminder_uses_memory_update_global_state(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "categories",
                    "onboarding_substep": "profile_collect",
                    "has_bank_accounts": True,
                    "has_imported_transactions": True,
                    "budget_created": True,
                    "bank_accounts_confirmed": True,
                }
            }
        },
    )
    loop = _LoopWithMemoryUpdate(
        {
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "import",
                    "onboarding_substep": "import_select_account",
                }
            }
        }
    )
    repo.bank_accounts = [{"id": "bank-1", "name": "UBS"}]
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "Salut"}, headers=_auth_headers())

    assert response.status_code == 200
    assert "(Pour continuer : indique le compte à importer.)" in response.json()["reply"]


class _RepoProfileFieldsRaises(_Repo):
    def get_profile_fields(self, *, profile_id: UUID, fields: list[str] | None = None) -> dict[str, object]:
        raise RuntimeError("profile lookup failed")


def test_onboarding_profile_get_profile_fields_failure_does_not_crash(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _RepoProfileFieldsRaises(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "profile",
                    "onboarding_substep": "profile_collect",
                }
            }
        },
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "Salut"}, headers=_auth_headers())

    assert response.status_code == 200
    assert "Pour démarrer" in response.json()["reply"]
