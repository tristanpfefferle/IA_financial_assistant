"""Tests for onboarding global state sequencing and persistence."""

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
        self.profile_fields = profile_fields or {"first_name": "Ada", "last_name": "Lovelace", "birth_date": "1815-12-10"}
        self.bank_accounts: list[dict[str, object]] = []
        self.profile_categories: list[dict[str, object]] = []
        self.merchants: list[dict[str, object]] = []
        self.update_calls: list[dict[str, object]] = []
        self.profile_update_calls: list[dict[str, object]] = []
        self.ensure_bank_accounts_calls: list[dict[str, object]] = []
        self.remove_bank_accounts_calls: list[dict[str, object]] = []
        self.sync_bank_accounts_calls: list[dict[str, object]] = []

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

    def remove_bank_accounts(self, *, profile_id: UUID, names: list[str]) -> dict[str, object]:
        assert profile_id == PROFILE_ID
        self.remove_bank_accounts_calls.append({"names": list(names)})
        names_lower = {name.lower() for name in names}
        kept_accounts: list[dict[str, object]] = []
        deleted: list[str] = []
        for account in self.bank_accounts:
            account_name = str(account.get("name") or "")
            if account_name.lower() in names_lower:
                deleted.append(account_name)
                continue
            kept_accounts.append(account)
        self.bank_accounts = kept_accounts
        return {"deleted": deleted}

    def sync_bank_accounts(self, *, profile_id: UUID, names: list[str]) -> dict[str, object]:
        assert profile_id == PROFILE_ID
        self.sync_bank_accounts_calls.append({"names": list(names)})
        desired_lower = {name.lower() for name in names}
        existing_lower = {str(row.get("name", "")).lower() for row in self.bank_accounts if row.get("name")}
        removed = [str(row.get("name")) for row in self.bank_accounts if str(row.get("name", "")).lower() not in desired_lower]
        self.bank_accounts = [row for row in self.bank_accounts if str(row.get("name", "")).lower() in desired_lower]
        created: list[str] = []
        for name in names:
            lowered = name.lower()
            if lowered in existing_lower:
                continue
            self.bank_accounts.append({"id": f"bank-{len(self.bank_accounts)+1}", "name": name})
            created.append(name)
            existing_lower.add(lowered)
        return {"all": list(names), "removed": removed, "created": created}

    def list_profile_categories(self, *, profile_id: UUID) -> list[dict[str, object]]:
        assert profile_id == PROFILE_ID
        return [dict(row) for row in self.profile_categories]

    def ensure_system_categories(self, *, profile_id: UUID, categories: list[dict[str, str]]) -> dict[str, int]:
        assert profile_id == PROFILE_ID
        existing_keys = {str(row.get("system_key")) for row in self.profile_categories if row.get("system_key")}
        existing_name_norms = {str(row.get("name_norm")) for row in self.profile_categories if row.get("name_norm")}
        created_count = 0
        for category in categories:
            system_key = category["system_key"]
            name = category["name"]
            name_norm = " ".join(name.strip().lower().split())
            if system_key in existing_keys or name_norm in existing_name_norms:
                continue
            self.profile_categories.append(
                {
                    "id": f"cat-{len(self.profile_categories)+1}",
                    "name": name,
                    "name_norm": name_norm,
                    "system_key": system_key,
                    "is_system": True,
                    "scope": "personal",
                }
            )
            existing_keys.add(system_key)
            existing_name_norms.add(name_norm)
            created_count += 1
        return {"created_count": created_count, "system_total_count": len(existing_keys)}

    def list_merchants_without_category(self, *, profile_id: UUID) -> list[dict[str, object]]:
        assert profile_id == PROFILE_ID
        return [dict(row) for row in self.merchants if not str(row.get("category") or "").strip()]

    def update_merchant_category(self, *, merchant_id: UUID, category_name: str) -> None:
        merchant_id_str = str(merchant_id)
        for merchant in self.merchants:
            if str(merchant.get("id")) == merchant_id_str:
                merchant["category"] = category_name
                return
        raise AssertionError("merchant not found")


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
    assert response.json()["tool_result"] == {"type": "ui_action", "action": "quick_replies", "options": [{"id": "yes", "label": "Oui, c'est tout bon, on peut continuer !", "value": "oui"}, {"id": "no", "label": "Non, je dois modifier quelque chose.", "value": "non"}]}
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


def test_profile_confirm_no_moves_to_profile_fix_select(monkeypatch) -> None:
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
    payload = response.json()
    assert payload["tool_result"]["type"] == "ui_action"
    assert payload["tool_result"]["action"] == "quick_replies"
    assert [opt["label"] for opt in payload["tool_result"]["options"]] == [
        "Prénom / Nom",
        "Date de naissance",
    ]
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_substep"] == "profile_fix_select"


def test_profile_confirm_unrecognized_answer_stays_on_confirm_with_yes_no_quick_replies(monkeypatch) -> None:
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

    response = client.post("/agent/chat", json={"message": "peut-être"}, headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["tool_result"]["type"] == "ui_action"
    assert payload["tool_result"]["action"] == "quick_replies"
    assert [opt["label"] for opt in payload["tool_result"]["options"]] == ["✅", "❌"]
    if repo.update_calls:
        persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    else:
        persisted = repo.chat_state["state"]["global_state"]
    assert persisted["onboarding_substep"] == "profile_confirm"


def test_profile_confirm_substep_is_downgraded_when_birth_date_missing(monkeypatch) -> None:
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
        profile_fields={"first_name": "Ada", "last_name": "Lovelace", "birth_date": None},
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "Salut"}, headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"] == "Merci Ada 🙂\n\nQuelle est ta date de naissance ?"
    assert payload["tool_result"]["form_id"] == "onboarding_profile_birth_date"
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_substep"] == "profile_collect"


def test_session_resume_allons_y_downgrades_profile_confirm_when_birth_date_missing(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "profile",
                    "onboarding_substep": "profile_confirm",
                },
                "session_resume_pending": True,
            }
        },
        profile_fields={"first_name": "Ada", "last_name": "Lovelace", "birth_date": None},
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "allons-y"}, headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["tool_result"]["form_id"] == "onboarding_profile_birth_date"
    persisted_state = repo.update_calls[-1]["chat_state"]["state"]
    assert persisted_state["session_resume_pending"] is False
    assert persisted_state["global_state"]["onboarding_substep"] == "profile_collect"


def test_session_resume_allons_y_from_profile_fix_returns_profile_confirm_recap(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "profile",
                    "onboarding_substep": "profile_fix_select",
                },
                "session_resume_pending": True,
            }
        },
        profile_fields={"first_name": "Ada", "last_name": "Lovelace", "birth_date": "1815-12-10"},
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "allons-y"}, headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert "Récapitulatif de ton profil" in payload["reply"]
    assert "Est-ce bien correct" in payload["reply"]
    assert payload["tool_result"]["options"] == [
        {"id": "yes", "label": "Oui, c'est tout bon, on peut continuer !", "value": "oui"},
        {"id": "no", "label": "Non, je dois modifier quelque chose.", "value": "non"},
    ]
    persisted_state = repo.update_calls[-1]["chat_state"]["state"]
    assert persisted_state["session_resume_pending"] is False
    assert persisted_state["global_state"]["onboarding_substep"] == "profile_confirm"

def test_session_resume_allons_y_from_bank_fix_returns_bank_confirm_recap(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "bank_accounts",
                    "onboarding_substep": "bank_accounts_fix_select",
                },
                "session_resume_pending": True,
            }
        },
    )
    repo.bank_accounts = [{"id": "bank-1", "name": "UBS"}, {"id": "bank-2", "name": "Revolut"}]
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "allons-y"}, headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert "Tu as des comptes bancaires chez" in payload["reply"]
    assert "Est-ce bien correct" in payload["reply"]
    assert payload["tool_result"]["action"] == "quick_replies"
    persisted_state = repo.update_calls[-1]["chat_state"]["state"]
    assert persisted_state["session_resume_pending"] is False
    assert persisted_state["global_state"]["onboarding_substep"] == "bank_accounts_confirm"



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
    assert repo.ensure_bank_accounts_calls == []
    payload = response.json()
    assert payload["tool_result"]["type"] == "ui_action"
    assert payload["tool_result"]["action"] == "form"
    assert payload["tool_result"]["form_id"] == "onboarding_bank_accounts"


def test_bank_accounts_confirm_no_back_to_collect(monkeypatch) -> None:
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
    assert persisted["onboarding_substep"] == "bank_accounts_collect"
    assert persisted["bank_accounts_confirmed"] is False
    assert "Modifie ta sélection" in response.json()["reply"]


def test_bank_accounts_confirm_yes_moves_to_import_wait_ready(monkeypatch) -> None:
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
    assert persisted["onboarding_step"] == "import"
    assert persisted["onboarding_substep"] == "import_wait_ready"
    assert persisted["bank_accounts_confirmed"] is True
    assert response.json()["tool_result"] == {
        "type": "ui_action",
        "action": "quick_replies",
        "options": [
            {"id": "import_ready_yes", "label": "Je suis prêt à te le transmettre !", "value": "import_ready_yes"},
            {"id": "import_ready_help", "label": "J’ai besoin de plus d’informations avant.", "value": "import_ready_help"},
        ],
    }


def test_bank_accounts_collect_no_with_existing_account_moves_to_import_select(monkeypatch) -> None:
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
    repo.bank_accounts = [{"id": "bank-1", "name": "UBS"}]
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "non"}, headers=_auth_headers())

    assert response.status_code == 200
    assert "Tu as des comptes bancaires chez : UBS." in response.json()["reply"]
    assert "nom exact" not in response.json()["reply"].lower()
    assert response.json()["tool_result"] == {"type": "ui_action", "action": "quick_replies", "options": [{"id": "yes", "label": "Oui, c'est correct !", "value": "oui"}, {"id": "no", "label": "Non, je dois modifier mon choix.", "value": "non"}]}
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_step"] == "bank_accounts"
    assert persisted["onboarding_substep"] == "bank_accounts_confirm"
    assert persisted["bank_accounts_confirmed"] is False
    assert persisted["has_bank_accounts"] is True


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
    payload = response.json()
    persisted_state = repo.update_calls[-1]["chat_state"]["state"]
    assert persisted_state["import_context"]["selected_bank_account_id"] == "bank-1"
    assert persisted_state["import_context"]["selected_bank_account_name"] == "UBS"
    assert payload["tool_result"]["type"] == "ui_request"
    assert payload["tool_result"]["name"] == "import_file"
    assert "envoie-moi le fichier csv" in payload["reply"].lower()
    assert loop.called is False


def test_categories_bootstrap_creates_categories_classifies_merchants_and_skips_loop(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "categories",
                    "onboarding_substep": "categories_bootstrap",
                    "profile_confirmed": True,
                    "bank_accounts_confirmed": True,
                    "has_bank_accounts": True,
                    "has_imported_transactions": True,
                    "budget_created": False,
                }
            }
        },
    )
    repo.bank_accounts = [{"id": "bank-1", "name": "UBS"}]
    repo.merchants = [
        {"id": UUID("11111111-1111-1111-1111-111111111111"), "name_norm": "migros geneve", "name": "Migros Genève", "category": None},
        {"id": UUID("22222222-2222-2222-2222-222222222222"), "name_norm": "inconnu sa", "name": "Inconnu SA", "category": ""},
        {"id": "not-a-uuid", "name_norm": "should skip", "name": "Should Skip", "category": ""},
    ]
    loop = _LoopSpy()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "go"}, headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"] == (
        "Import terminé ✅\n\n"
        "Je viens de générer ton premier rapport financier.\n"
        "Ouvre-le, puis dis-moi quand tu l’as consulté 🙂"
    )
    assert len(repo.profile_categories) == len(agent_api._SYSTEM_CATEGORIES)
    assert repo.merchants[0]["category"] == "Alimentation"
    assert repo.merchants[1]["category"] == "Autres"
    assert repo.merchants[2]["category"] == ""
    assert payload["tool_result"]["type"] == "ui_request"
    assert payload["tool_result"]["name"] == "open_pdf_report"
    assert payload["tool_result"]["quick_replies"] == [{"id": "seen", "label": "J’ai consulté mon rapport", "value": "j_ai_consulte_mon_rapport"}]
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["mode"] == "onboarding"
    assert persisted["onboarding_step"] == "report"
    assert persisted["onboarding_substep"] == "report_wait_view_confirmation"
    assert loop.called is False


def test_import_classification_direct_to_pdf_when_already_classified(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "categories",
                    "onboarding_substep": "categories_bootstrap",
                    "profile_confirmed": True,
                    "bank_accounts_confirmed": True,
                    "has_bank_accounts": True,
                    "has_imported_transactions": True,
                    "budget_created": False,
                }
            }
        },
    )
    repo.bank_accounts = [{"id": "bank-1", "name": "UBS"}]
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": ""}, headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert "Import terminé ✅" in payload["reply"]
    assert payload["tool_result"]["type"] == "ui_request"
    assert payload["tool_result"]["name"] == "open_pdf_report"
    assert payload["tool_result"]["quick_replies"] == [{"id": "seen", "label": "J’ai consulté mon rapport", "value": "j_ai_consulte_mon_rapport"}]




def test_classify_merchants_without_category_invalid_ids_not_counted_as_remaining() -> None:

    repo = _Repo()
    repo.merchants = [
        {"id": UUID("11111111-1111-1111-1111-111111111111"), "name_norm": "coop city", "name": "Coop City", "category": ""},
        {"id": "not-a-uuid", "name_norm": "broken", "name": "Broken", "category": ""},
    ]

    classified_count, remaining_count, invalid_count = agent_api._classify_merchants_without_category(
        profiles_repository=repo,
        profile_id=PROFILE_ID,
    )

    assert classified_count == 1
    assert remaining_count == 0
    assert invalid_count == 1
    assert repo.merchants[0]["category"] == "Alimentation"
    assert repo.merchants[1]["category"] == ""


def test_free_chat_rapport_pdf_generates_ui_request(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "free_chat",
                    "onboarding_step": None,
                    "onboarding_substep": None,
                    "has_bank_accounts": True,
                    "has_imported_transactions": True,
                }
            }
        },
    )
    loop = _LoopSpy()
    repo.bank_accounts = [{"id": "bank-1", "name": "UBS"}]
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "rapport pdf janvier 2026"}, headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["tool_result"]["type"] == "ui_request"
    assert payload["tool_result"]["name"] == "open_pdf_report"
    assert "month=2026-01" in payload["tool_result"]["url"]
    assert payload["plan"]["tool_name"] == "finance_report_spending_pdf"
    assert payload["plan"]["payload"]["month"] == "2026-01"
    assert loop.called is False


@pytest.mark.parametrize(
    ("step", "substep"),
    [
        ("profile", "profile_collect"),
        ("bank_accounts", "bank_accounts_collect"),
        ("categories", None),
        ("budget", None),
        ("report", None),
        (None, None),
    ],
)
def test_non_import_steps_do_not_return_import_ui_request(monkeypatch, step: str | None, substep: str | None) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding" if step is not None else "free_chat",
                    "onboarding_step": step,
                    "onboarding_substep": substep,
                    "has_bank_accounts": True,
                },
                "import_context": {
                    "selected_bank_account_id": "bank-1",
                    "selected_bank_account_name": "UBS",
                },
            }
        },
        profile_fields={"first_name": "Ada", "last_name": "Lovelace", "birth_date": "1815-12-10"},
    )
    loop = _LoopWithGlobal()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "Bonjour"}, headers=_auth_headers())

    assert response.status_code == 200
    tool_result = response.json()["tool_result"]
    if isinstance(tool_result, dict):
        assert not (tool_result.get("type") == "ui_request" and tool_result.get("name") == "import_file")


def test_onboarding_reply_from_loop_skips_reminder_for_categories_step(monkeypatch) -> None:
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
    assert "(Pour continuer" not in response.json()["reply"]


class _RepoWithoutListBankAccounts(_Repo):
    def __getattribute__(self, name: str):
        if name == "list_bank_accounts":
            raise AttributeError(name)
        return super().__getattribute__(name)


class _RepoWithoutGetProfileFields(_Repo):
    def __getattribute__(self, name: str):
        if name == "get_profile_fields":
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
    assert persisted["onboarding_substep"] == "profile_intro"
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
    assert response.json()["reply"].startswith("loop")
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
    assert response.json()["reply"].startswith("loop")
    assert repo.update_calls == []
    assert loop.called is True


def test_free_chat_re_gates_to_import_when_transactions_not_imported(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "free_chat",
                    "onboarding_step": None,
                    "onboarding_substep": None,
                    "has_bank_accounts": True,
                    "has_imported_transactions": False,
                    "budget_created": False,
                },
                "import_context": {
                    "selected_bank_account_id": "bank-1",
                    "selected_bank_account_name": "UBS",
                },
            }
        },
    )
    repo.bank_accounts = [{"id": "bank-1", "name": "UBS"}]
    loop = _LoopSpy()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "Salut"}, headers=_auth_headers())

    assert response.status_code == 200
    assert "Quand ton fichier est prêt à être importé" in response.json()["reply"]
    persisted_state = repo.update_calls[-1]["chat_state"]["state"]
    assert persisted_state["global_state"]["onboarding_step"] == "import"
    assert persisted_state["global_state"]["onboarding_substep"] == "import_wait_ready"
    assert persisted_state["global_state"]["has_imported_transactions"] is False
    assert "import_context" not in persisted_state
    assert loop.called is False


def test_free_chat_does_not_re_gate_import_when_transactions_already_imported(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "free_chat",
                    "onboarding_step": None,
                    "onboarding_substep": None,
                    "has_bank_accounts": True,
                    "has_imported_transactions": True,
                    "budget_created": False,
                }
            }
        },
    )
    repo.bank_accounts = [{"id": "bank-1", "name": "UBS"}]
    loop = _LoopSpy()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "Salut"}, headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()["reply"].startswith("loop")
    assert repo.update_calls == []
    assert loop.called is True


def test_free_chat_does_not_re_gate_import_when_bank_account_check_unavailable(monkeypatch) -> None:
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
    assert response.json()["reply"].startswith("loop")
    assert repo.update_calls == []
    assert loop.called is True


def test_onboarding_reminder_uses_memory_update_global_state(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "free_chat",
                    "onboarding_step": None,
                    "onboarding_substep": None,
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
                    "onboarding_substep": "profile_intro",
                }
            }
        },
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "Salut"}, headers=_auth_headers())

    assert response.status_code == 200
    assert "assistant financier" in response.json()["reply"].lower()


class _LoopWithoutGlobal:
    def __init__(self) -> None:
        self.called = False

    def handle_user_message(self, _message: str, *, profile_id=None, active_task=None, memory=None) -> AgentReply:
        self.called = True
        return AgentReply(reply="ok-without-global")


def test_bootstrap_onboarding_bank_accounts_if_profile_complete(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={"state": {}},
        profile_fields={"first_name": "Ada", "last_name": "Lovelace", "birth_date": "1815-12-10"},
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "Bonjour"}, headers=_auth_headers())

    assert response.status_code == 200
    assert repo.update_calls
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_step"] == "profile"
    assert persisted["onboarding_substep"] == "profile_confirm"


def test_does_not_pass_global_state_when_loop_handler_does_not_accept_it(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "free_chat",
                    "onboarding_step": None,
                    "onboarding_substep": None,
                    "has_bank_accounts": True,
                    "has_imported_transactions": True,
                    "budget_created": True,
                }
            }
        },
    )
    repo.bank_accounts = [{"id": "bank-1", "name": "UBS"}]
    loop = _LoopWithoutGlobal()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "Salut"}, headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()["reply"] == "ok-without-global"
    assert loop.called is True


def test_free_chat_re_gates_to_profile_when_profile_missing(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "free_chat",
                    "onboarding_step": None,
                    "onboarding_substep": None,
                    "has_bank_accounts": True,
                }
            }
        },
        profile_fields={"first_name": None, "last_name": "X", "birth_date": None},
    )
    loop = _LoopSpy()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "Salut"}, headers=_auth_headers())

    assert response.status_code == 200
    assert loop.called is False
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["mode"] == "onboarding"
    assert persisted["onboarding_step"] == "profile"
    assert persisted["onboarding_substep"] == "profile_intro"
    assert "assistant financier" in response.json()["reply"].lower()
    assert response.json()["tool_result"]["options"] == [{"id": "start", "label": "Allons-y !", "value": "allons-y"}]


def test_profile_re_gate_message_asks_only_for_name(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "free_chat",
                    "onboarding_step": None,
                    "onboarding_substep": None,
                    "has_bank_accounts": True,
                }
            }
        },
        profile_fields={"first_name": None, "last_name": None, "birth_date": None},
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "Salut"}, headers=_auth_headers())

    assert response.status_code == 200
    reply = response.json()["reply"].lower()
    assert "assistant financier" in reply
    assert "date de naissance" not in reply


def test_onboarding_import_re_gates_to_profile_when_profile_missing(monkeypatch) -> None:
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
        profile_fields={"first_name": "Ada", "last_name": None, "birth_date": None},
    )
    loop = _LoopSpy()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "Continuer"}, headers=_auth_headers())

    assert response.status_code == 200
    assert loop.called is False
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_step"] == "profile"
    assert persisted["onboarding_substep"] == "profile_intro"


def test_profile_check_is_skipped_when_repo_has_no_get_profile_fields(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _RepoWithoutGetProfileFields(
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
    repo.bank_accounts = [{"id": "bank-1", "name": "UBS"}]
    loop = _LoopSpy()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "Salut"}, headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()["reply"].startswith("loop")
    assert loop.called is True
    assert repo.update_calls == []


def test_profile_confirmed_is_reset_when_profile_missing(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "bank_accounts",
                    "onboarding_substep": "bank_accounts_collect",
                    "profile_confirmed": True,
                }
            }
        },
        profile_fields={"first_name": "Ada", "last_name": None, "birth_date": "1815-12-10"},
    )
    loop = _LoopSpy()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "Salut"}, headers=_auth_headers())

    assert response.status_code == 200
    assert loop.called is False
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_step"] == "profile"
    assert persisted["onboarding_substep"] == "profile_intro"
    assert persisted["profile_confirmed"] is False


def test_free_chat_re_gates_to_bank_accounts_collect_when_no_accounts(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
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
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "Salut"}, headers=_auth_headers())

    assert response.status_code == 200
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["mode"] == "onboarding"
    assert persisted["onboarding_step"] == "bank_accounts"
    assert persisted["onboarding_substep"] == "bank_accounts_collect"
    assert response.json()["reply"] == "Avant de continuer, indique-moi ta/tes banques (ex: ‘UBS, Revolut’)."



def test_onboarding_profile_collect_ignores_free_text_updates(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "profile",
                    "onboarding_substep": "profile_collect",
                }
            }
        },
        profile_fields={"first_name": None, "last_name": None, "birth_date": None},
    )
    loop = _LoopSpy()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "Tristan Pfefferlé"}, headers=_auth_headers())

    assert response.status_code == 200
    assert repo.profile_update_calls == []
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_substep"] == "profile_collect"
    assert response.json()["reply"] == "Renseigne ton prénom et ton nom."
    assert response.json()["tool_result"]["action"] == "form"
    assert response.json()["tool_result"]["form_id"] == "onboarding_profile_name"
    assert loop.called is False


def test_onboarding_profile_collect_complete_profile_shows_recap(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "profile",
                    "onboarding_substep": "profile_collect",
                }
            }
        },
        profile_fields={"first_name": "Paul", "last_name": "Gorok", "birth_date": "1994-01-10"},
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "n'importe quoi"}, headers=_auth_headers())

    assert response.status_code == 200
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_substep"] == "profile_confirm"
    assert "récapitulatif de ton profil" in response.json()["reply"].lower()


def test_onboarding_profile_non_profile_message_returns_help_and_skips_loop(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "profile",
                    "onboarding_substep": "profile_collect",
                }
            }
        },
        profile_fields={"first_name": None, "last_name": None, "birth_date": None},
    )
    loop = _LoopSpy()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "Liste mes catégories"}, headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()["reply"] == "Renseigne ton prénom et ton nom."
    assert loop.called is False


def test_profile_collect_with_missing_birth_date_stays_collect(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "profile",
                    "onboarding_substep": "profile_collect",
                }
            }
        },
        profile_fields={"first_name": "Tristan", "last_name": "Pfefferlé", "birth_date": None},
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response_collect = client.post("/agent/chat", json={"message": "1992-01-15"}, headers=_auth_headers())
    assert response_collect.status_code == 200
    assert repo.profile_update_calls == []
    persisted_collect = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted_collect["onboarding_substep"] == "profile_collect"
    assert response_collect.json()["reply"] == "Quelle est ta date de naissance ?"
    assert response_collect.json()["tool_result"]["form_id"] == "onboarding_profile_birth_date"

    

def test_onboarding_bank_accounts_collect_form_returned(monkeypatch) -> None:
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
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "Salut"}, headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["tool_result"]["type"] == "ui_action"
    assert payload["tool_result"]["action"] == "form"
    assert payload["tool_result"]["form_id"] == "onboarding_bank_accounts"
    assert [field["id"] for field in payload["tool_result"]["fields"]] == ["selected_banks"]


def test_onboarding_bank_accounts_submit_moves_to_confirm_and_returns_recap(monkeypatch) -> None:
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
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    message = '__ui_form_submit__:{"form_id":"onboarding_bank_accounts","values":{"selected_banks":["UBS","Revolut"]}}'
    response = client.post("/agent/chat", json={"message": message}, headers=_auth_headers())

    assert response.status_code == 200
    assert repo.sync_bank_accounts_calls == [{"names": ["UBS", "Revolut"]}]
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_step"] == "bank_accounts"
    assert persisted["onboarding_substep"] == "bank_accounts_confirm"
    assert "Tu as des comptes bancaires chez" in response.json()["reply"]
    assert response.json()["tool_result"]["action"] == "quick_replies"






def test_onboarding_bank_accounts_submit_replaces_existing_selection(monkeypatch) -> None:
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
    )
    repo.bank_accounts = [
        {"id": "bank-1", "name": "UBS"},
        {"id": "bank-2", "name": "BCV"},
    ]
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    message = '__ui_form_submit__:{"form_id":"onboarding_bank_accounts","values":{"selected_banks":["UBS"]}}'
    response = client.post("/agent/chat", json={"message": message}, headers=_auth_headers())

    assert response.status_code == 200
    assert [account["name"] for account in repo.list_bank_accounts(profile_id=PROFILE_ID)] == ["UBS"]
    assert repo.sync_bank_accounts_calls == [{"names": ["UBS"]}]

def test_onboarding_bank_accounts_confirm_without_accounts_forces_collect(monkeypatch) -> None:
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
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "oui"}, headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["tool_result"]["action"] == "form"
    assert payload["tool_result"]["form_id"] == "onboarding_bank_accounts"
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_substep"] == "bank_accounts_collect"
    assert persisted["has_bank_accounts"] is False

def test_onboarding_bank_accounts_confirm_yes_moves_to_import_step(monkeypatch) -> None:
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
    )
    repo.bank_accounts = [{"id": "bank-1", "name": "UBS"}]
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "oui"}, headers=_auth_headers())

    assert response.status_code == 200
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_step"] == "import"
    assert persisted["onboarding_substep"] == "import_wait_ready"
    assert persisted["bank_accounts_confirmed"] is True



def test_onboarding_reminder_ignores_invalid_memory_update_state(monkeypatch) -> None:
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
                    "budget_created": False,
                    "profile_confirmed": True,
                    "bank_accounts_confirmed": True,
                }
            }
        },
    )
    repo.bank_accounts = [{"id": "bank-1", "name": "UBS"}]
    loop = _LoopWithMemoryUpdate({"state": None, "global_state": "ignored"})
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "Salut"}, headers=_auth_headers())

    assert response.status_code == 200
    assert "(Pour continuer" not in response.json()["reply"]



def test_onboarding_request_greeting_returns_profile_intro_without_session_resume(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "profile",
                    "onboarding_substep": "profile_intro",
                }
            }
        },
        profile_fields={"first_name": "", "last_name": "", "birth_date": ""},
    )
    loop = _LoopSpy()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post(
        "/agent/chat",
        json={"message": "", "request_greeting": True},
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert "Je suis ton assistant financier" in response.json()["reply"]
    assert response.json()["tool_result"]["options"] == [{"id": "start", "label": "Allons-y !", "value": "allons-y"}]
    assert "session_resume_pending" not in repo.chat_state.get("state", {})
    assert loop.called is False


def test_profile_intro_allons_y_returns_name_form(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "profile",
                    "onboarding_substep": "profile_intro",
                }
            }
        },
        profile_fields={"first_name": "", "last_name": "", "birth_date": ""},
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "allons-y"}, headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["tool_result"]["form_id"] == "onboarding_profile_name"
    assert repo.update_calls[-1]["chat_state"]["state"]["global_state"]["onboarding_substep"] == "profile_collect"



def test_onboarding_resume_pending_allons_y_profile_confirm_returns_recap(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "profile",
                    "onboarding_substep": "profile_confirm",
                },
                "session_resume_pending": True,
            }
        },
        profile_fields={"first_name": "Ada", "last_name": "Lovelace", "birth_date": "1815-12-10"},
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "allons-y"}, headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert "Récapitulatif de ton profil" in payload["reply"]
    assert "Est-ce bien correct" in payload["reply"]
    assert payload["tool_result"]["options"] == [{"id": "yes", "label": "Oui, c'est tout bon, on peut continuer !", "value": "oui"}, {"id": "no", "label": "Non, je dois modifier quelque chose.", "value": "non"}]
    assert repo.update_calls[-1]["chat_state"]["state"]["session_resume_pending"] is False


def test_onboarding_resume_pending_allons_y_resumes_profile_collect(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "profile",
                    "onboarding_substep": "profile_collect",
                },
                "session_resume_pending": True,
            }
        },
        profile_fields={"first_name": "Ada", "last_name": "Lovelace", "birth_date": ""},
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "allons-y"}, headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"] == "Quelle est ta date de naissance ?"
    assert payload["tool_result"]["form_id"] == "onboarding_profile_birth_date"
    assert repo.update_calls[-1]["chat_state"]["state"]["session_resume_pending"] is False


def test_onboarding_birth_date_form_rejects_implausible_date(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "profile",
                    "onboarding_substep": "profile_collect",
                }
            }
        },
        profile_fields={"first_name": "Ada", "last_name": "Lovelace", "birth_date": ""},
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post(
        "/agent/chat",
        json={"message": '__ui_form_submit__:{"form_id":"onboarding_profile_birth_date","values":{"birth_date":"1800-01-01"}}'},
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert response.json()["reply"] == "Cette date me paraît étrange. Peux-tu vérifier ?"
    assert response.json()["tool_result"]["form_id"] == "onboarding_profile_birth_date"
    assert all("birth_date" not in call for call in repo.profile_update_calls)

def test_profile_fix_name_submit_keeps_birth_date_and_returns_recap(monkeypatch) -> None:
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

    client.post("/agent/chat", json={"message": "non"}, headers=_auth_headers())
    client.post("/agent/chat", json={"message": "corriger_nom"}, headers=_auth_headers())
    submit = client.post(
        "/agent/chat",
        json={
            "message": '__ui_form_submit__:{"form_id":"onboarding_profile_name","values":{"first_name":"Augusta","last_name":"King"}}'
        },
        headers=_auth_headers(),
    )

    assert submit.status_code == 200
    payload = submit.json()
    assert payload["tool_result"]["action"] == "quick_replies"
    assert "Quelle est ta date de naissance ?" not in payload["reply"]
    assert "Date de naissance: 10 décembre 1815" in payload["reply"]


def test_profile_recap_formats_birth_date_in_french(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "profile",
                    "onboarding_substep": "profile_collect",
                }
            }
        },
        profile_fields={"first_name": "Ada", "last_name": "Lovelace", "birth_date": ""},
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post(
        "/agent/chat",
        json={
            "message": '__ui_form_submit__:{"form_id":"onboarding_profile_birth_date","values":{"birth_date":"1995-05-10"}}'
        },
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert "Date de naissance: 10 mai 1995" in response.json()["reply"]



def test_profile_collect_no_longer_persists_from_free_text(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "profile",
                    "onboarding_substep": "profile_collect",
                }
            }
        },
        profile_fields={"first_name": "", "last_name": "", "birth_date": ""},
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "Je m'appelle Tristan"}, headers=_auth_headers())

    assert response.status_code == 200
    assert repo.profile_update_calls == []
    assert response.json()["reply"] == "Renseigne ton prénom et ton nom."
    assert response.json()["tool_result"]["action"] == "form"
    assert response.json()["tool_result"]["form_id"] == "onboarding_profile_name"
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_substep"] == "profile_collect"


def test_profile_form_submit_name_updates_repo_and_returns_birth_date_form(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "profile",
                    "onboarding_substep": "profile_collect",
                }
            }
        },
        profile_fields={"first_name": "", "last_name": "", "birth_date": ""},
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post(
        "/agent/chat",
        json={
            "message": '__ui_form_submit__:{"form_id":"onboarding_profile_name","values":{"first_name":"Tristan","last_name":"Pfefferlé"}}'
        },
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert {"first_name": "Tristan", "last_name": "Pfefferlé"} in repo.profile_update_calls
    payload = response.json()
    assert payload["reply"] == "Quelle est ta date de naissance ?"
    assert payload["tool_result"]["type"] == "ui_action"
    assert payload["tool_result"]["action"] == "form"
    assert payload["tool_result"]["form_id"] == "onboarding_profile_birth_date"


def test_profile_form_submit_name_detects_marker_after_human_text(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "profile",
                    "onboarding_substep": "profile_collect",
                }
            }
        },
        profile_fields={"first_name": "", "last_name": "", "birth_date": ""},
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post(
        "/agent/chat",
        json={
            "message": 'Je m\'appelle Tristan Pfefferlé.\n__ui_form_submit__:{"form_id":"onboarding_profile_name","values":{"first_name":"Tristan","last_name":"Pfefferlé"}}'
        },
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert {"first_name": "Tristan", "last_name": "Pfefferlé"} in repo.profile_update_calls
    assert response.json()["reply"] == "Quelle est ta date de naissance ?"


def test_profile_form_submit_birth_date_moves_to_confirm_with_yes_no(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "profile",
                    "onboarding_substep": "profile_collect",
                }
            }
        },
        profile_fields={"first_name": "Tristan", "last_name": "Pfefferlé", "birth_date": ""},
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post(
        "/agent/chat",
        json={
            "message": '__ui_form_submit__:{"form_id":"onboarding_profile_birth_date","values":{"birth_date":"2001-12-22"}}'
        },
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert {"birth_date": "2001-12-22"} in repo.profile_update_calls
    payload = response.json()
    assert "Récapitulatif de ton profil" in payload["reply"]
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_substep"] == "profile_confirm"
    assert payload["tool_result"] == {
        "type": "ui_action",
        "action": "quick_replies",
        "options": [{"id": "yes", "label": "Oui, c'est tout bon, on peut continuer !", "value": "oui"}, {"id": "no", "label": "Non, je dois modifier quelque chose.", "value": "non"}],
    }


def test_profile_collect_when_profile_complete_shows_recap(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "profile",
                    "onboarding_substep": "profile_collect",
                }
            }
        },
        profile_fields={"first_name": "Paul", "last_name": "Murt", "birth_date": "1990-01-01"},
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "ok"}, headers=_auth_headers())

    assert response.status_code == 200
    assert "récapitulatif de ton profil" in response.json()["reply"].lower()
    assert response.json()["tool_result"] == {
        "type": "ui_action",
        "action": "quick_replies",
        "options": [{"id": "yes", "label": "Oui, c'est tout bon, on peut continuer !", "value": "oui"}, {"id": "no", "label": "Non, je dois modifier quelque chose.", "value": "non"}],
    }
def test_profile_confirmation_no_returns_profile_fix_quick_replies(monkeypatch) -> None:
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

    response = client.post("/agent/chat", json={"message": "❌"}, headers=_auth_headers())
    payload = response.json()

    assert payload["reply"] == "Pas de souci 🙂 Qu’est-ce que tu veux corriger ?"
    assert "Réponds simplement par 1 ou 2" not in payload["reply"]
    assert payload["tool_result"] is not None
    assert payload["tool_result"]["type"] == "ui_action"
    assert payload["tool_result"]["action"] == "quick_replies"
    assert [opt["label"] for opt in payload["tool_result"]["options"]] == [
        "Prénom / Nom",
        "Date de naissance",
    ]
    assert [opt["value"] for opt in payload["tool_result"]["options"]] == ["corriger_nom", "corriger_date"]

    persisted_global = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted_global["onboarding_substep"] == "profile_fix_select"



def test_profile_fix_select_name_resets_only_names(monkeypatch) -> None:
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

    client.post("/agent/chat", json={"message": "non"}, headers=_auth_headers())
    second = client.post("/agent/chat", json={"message": "corriger_nom"}, headers=_auth_headers())

    assert {"first_name": "", "last_name": ""} not in repo.profile_update_calls
    assert repo.profile_fields["birth_date"] == "1815-12-10"
    assert second.json()["reply"] == "Renseigne ton prénom et ton nom."
    assert second.json()["tool_result"]["form_id"] == "onboarding_profile_name"



def test_profile_fix_select_birth_date_resets_only_birth_date(monkeypatch) -> None:
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

    client.post("/agent/chat", json={"message": "non"}, headers=_auth_headers())
    second = client.post("/agent/chat", json={"message": "corriger_date"}, headers=_auth_headers())

    assert {"birth_date": ""} not in repo.profile_update_calls
    assert repo.profile_fields["first_name"] == "Ada"
    assert repo.profile_fields["last_name"] == "Lovelace"
    assert second.json()["reply"] == "Quelle est ta date de naissance ?"
    assert second.json()["tool_result"]["form_id"] == "onboarding_profile_birth_date"


def test_after_bank_added_waits_ready_before_import_ui_request(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={"state": {"global_state": {"mode": "onboarding", "onboarding_step": "bank_accounts", "onboarding_substep": "bank_accounts_collect"}}},
        profile_fields={"first_name": "Ada", "last_name": "Lovelace", "birth_date": "1815-12-10"},
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "UBS"}, headers=_auth_headers())
    payload = response.json()
    assert payload["tool_result"] is not None
    assert payload["tool_result"]["type"] == "ui_action"
    assert payload["tool_result"]["action"] == "form"
    assert payload["tool_result"]["form_id"] == "onboarding_bank_accounts"


def test_import_wait_ready_confirmation_returns_import_file(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={"state": {"global_state": {"mode": "onboarding", "onboarding_step": "import", "onboarding_substep": "import_wait_ready"}}},
        profile_fields={"first_name": "Ada", "last_name": "Lovelace", "birth_date": "1815-12-10"},
    )
    repo.bank_accounts = [{"id": "bank-1", "name": "UBS"}]
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "import_ready_yes"}, headers=_auth_headers())
    payload = response.json()
    assert payload["reply"] == "Parfait 🙂\n\nClique sur « Importer maintenant » pour sélectionner ton fichier CSV."
    assert payload["tool_result"]["name"] == "import_file"
    assert payload["tool_result"]["accepted_types"] == ["csv"]


def test_import_wait_ready_help_enters_help_menu(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={"state": {"global_state": {"mode": "onboarding", "onboarding_step": "import", "onboarding_substep": "import_wait_ready"}}},
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post(
        "/agent/chat",
        json={"message": "import_ready_help"},
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"] == "Très bien, comment puis-je t'aider ?"
    assert payload["tool_result"]["options"] == [
        {
            "id": "help_csv",
            "label": "Je ne sais pas comment extraire un fichier .csv de mon e-banking.",
            "value": "import_help_csv",
        },
        {
            "id": "help_security",
            "label": "Est-ce que mes données bancaires sont collectées ?",
            "value": "import_help_security",
        },
    ]
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_substep"] == "import_help_menu"


def test_import_help_menu_csv_goes_to_bank_select(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={"state": {"global_state": {"mode": "onboarding", "onboarding_step": "import", "onboarding_substep": "import_help_menu"}}},
    )
    repo.bank_accounts = [{"id": "bank-1", "name": "UBS"}, {"id": "bank-2", "name": "Revolut"}]
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "import_help_csv"}, headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"] == "Chez quelle banque souhaites-tu extraire ces données ?"
    option_labels = [option["label"] for option in payload["tool_result"]["options"]]
    assert option_labels == ["UBS", "Revolut"]
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_substep"] == "import_help_bank_select"


def test_import_help_bank_select_then_back_to_import_shows_short_question_only(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={"state": {"global_state": {"mode": "onboarding", "onboarding_step": "import", "onboarding_substep": "import_help_bank_select"}}},
    )
    repo.bank_accounts = [{"id": "bank-1", "name": "UBS"}]
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    first = client.post("/agent/chat", json={"message": "import_help_bank:UBS"}, headers=_auth_headers())
    assert first.status_code == 200
    assert first.json()["tool_result"]["options"][0]["value"] == "import_help_back_to_import"

    second = client.post("/agent/chat", json={"message": "import_help_back_to_import"}, headers=_auth_headers())
    assert second.status_code == 200
    payload = second.json()
    assert payload["reply"] == "Ton fichier CSV est-il prêt pour l’import ?"
    assert payload["tool_result"]["options"] == [
        {"id": "import_ready_yes", "label": "Je suis prêt à te le transmettre !", "value": "import_ready_yes"},
        {
            "id": "import_ready_help",
            "label": "J’ai besoin de plus d’informations avant.",
            "value": "import_ready_help",
        },
    ]
    assert "la prochaine étape consiste à importer" not in payload["reply"].lower()


def test_import_help_security_then_back_to_menu(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={"state": {"global_state": {"mode": "onboarding", "onboarding_step": "import", "onboarding_substep": "import_help_menu"}}},
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    first = client.post("/agent/chat", json={"message": "import_help_security"}, headers=_auth_headers())
    assert first.status_code == 200
    assert "retirant toutes les données sensibles" in first.json()["reply"]

    second = client.post("/agent/chat", json={"message": "import_help_back_to_menu"}, headers=_auth_headers())
    assert second.status_code == 200
    payload = second.json()
    assert payload["reply"] == "Très bien, comment puis-je t'aider ?"
    assert [opt["value"] for opt in payload["tool_result"]["options"]] == ["import_help_csv", "import_help_security"]


def test_report_view_confirmation_enters_confidence_improvement(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "last_spending_report_payload": {
                    "categorization_confidence_score_percent": 76,
                    "categorization_confidence_coverage_percent": 67,
                },
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "report",
                    "onboarding_substep": "report_wait_view_confirmation",
                    "confidence_step": None,
                },
            }
        }
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "j_ai_consulte_mon_rapport"}, headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"] == (
        "Très bien ! Ce premier rapport donne une indication des entrées et sorties de ton compte et de la répartition de tes dépenses sur la période du relevé bancaire que tu as importé.\n\n"
        "Notre système utilise une catégorisation automatique par IA. Dans ton cas, le taux de précision estimé est de 76% — ça donne déjà une bonne idée d’où est parti ton argent.\n\n"
        "Pour rendre le rapport encore plus pertinent, j’aimerais te poser quelques questions. L’objectif est d’atteindre une précision supérieure à 95%, afin d’être dans les meilleures dispositions pour construire ton budget.\n\n"
        "Es-tu prêt ? Ça ne te prendra que quelques minutes 🙂"
    )
    assert payload["tool_result"]["options"] == [
        {"id": "yes", "label": "✅", "value": "oui"},
        {"id": "no", "label": "❌", "value": "non"},
    ]
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["mode"] == "confidence_improvement"
    assert persisted["onboarding_step"] is None
    assert persisted["onboarding_substep"] is None
    assert persisted["confidence_step"] == "waiting_start"


def test_confidence_waiting_start_yes_moves_to_shared_expenses_offer(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "confidence_improvement",
                    "onboarding_step": None,
                    "onboarding_substep": None,
                    "confidence_step": "waiting_start",
                }
            }
        }
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "oui"}, headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()["reply"] == (
        "Commençons par vérifier que les transactions de ton compte représentent bien ta réalité.\n\n"
        "Dans certaines situations (colocation, concubinage), une personne paie des dépenses communes puis se fait rembourser ensuite via des virements bancaires ou TWINT.\n\n"
        "Ce fonctionnement peut fausser la lecture du rapport. Pour gérer ça, on a un système de partage de transactions qui garantit une précision optimale pour chaque personne concernée.\n\n"
        "Es-tu intéressé par cette fonctionnalité ? (c’est surtout utile si ça arrive régulièrement chaque mois)"
    )
    assert response.json()["tool_result"]["options"] == [
        {"id": "yes", "label": "✅", "value": "oui"},
        {"id": "no", "label": "❌", "value": "non"},
    ]
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["mode"] == "confidence_improvement"
    assert persisted["confidence_step"] == "shared_expenses_offer"





def test_confidence_waiting_start_no_keeps_waiting_start(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "confidence_improvement",
                    "onboarding_step": None,
                    "onboarding_substep": None,
                    "confidence_step": "waiting_start",
                }
            }
        }
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "non"}, headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()["reply"] == "Ok 🙂 Dis-moi quand tu seras prêt."
    assert response.json()["tool_result"]["options"] == [
        {"id": "yes", "label": "✅", "value": "oui"},
        {"id": "no", "label": "❌", "value": "non"},
    ]
    assert repo.update_calls == []


def test_confidence_shared_expenses_offer_yes_starts_account_link_setup(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "confidence_improvement",
                    "onboarding_step": None,
                    "onboarding_substep": None,
                    "confidence_step": "shared_expenses_offer",
                }
            },
            "active_task": None,
        }
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "oui"}, headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()["reply"] == "Parfait 🙂 On va configurer le partage."
    persisted_chat_state = repo.update_calls[-1]["chat_state"]
    persisted_global_state = persisted_chat_state["state"]["global_state"]
    assert persisted_global_state["confidence_step"] is None
    assert persisted_chat_state["active_task"] == {
        "type": "account_link_setup",
        "step": "ask_has_shared_expenses",
        "draft": {},
    }

def test_reconnect_onboarding_substep_without_loop_answers_expected_question(monkeypatch) -> None:
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

    response = client.post("/agent/chat", json={"message": "Quelle question ?"}, headers=_auth_headers())

    assert response.status_code == 200
    reply = response.json()["reply"]
    assert "Confirme ton profil" in reply
    assert "On continue d'abord cette étape" not in reply

def test_loop_persistence_roundtrip_in_chat_state(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "profile",
                    "onboarding_substep": "profile_confirm",
                },
                "loop": {
                    "loop_id": "onboarding.profile_confirm",
                    "step": "start",
                    "data": {"from": "test"},
                    "blocking": True,
                },
            }
        },
        profile_fields={"first_name": "Ada", "last_name": "Lovelace", "birth_date": "1815-12-10"},
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "oui"}, headers=_auth_headers())

    assert response.status_code == 200
    persisted_state = repo.update_calls[-1]["chat_state"]["state"]
    assert persisted_state["loop"]["loop_id"] == "onboarding.bank_accounts_collect"
    assert persisted_state["loop"]["data"] == {"from": "test"}


def test_agent_chat_debug_payload_exposes_loop_context(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "profile",
                    "onboarding_substep": "profile_confirm",
                },
                "loop": {
                    "loop_id": "onboarding.profile_confirm",
                    "step": "start",
                    "data": {},
                    "blocking": True,
                },
            }
        }
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response_debug = client.post(
        "/agent/chat",
        json={"message": "peut-être"},
        headers={**_auth_headers(), "X-Debug": "1"},
    )
    assert response_debug.status_code == 200
    payload_debug = response_debug.json()
    assert payload_debug["debug"]["loop"]["loop_id"] == "onboarding.profile_confirm"

    response_no_debug = client.post("/agent/chat", json={"message": "peut-être"}, headers=_auth_headers())
    assert response_no_debug.status_code == 200
    payload_no_debug = response_no_debug.json()
    assert payload_no_debug.get("debug") is None




def test_agent_chat_debug_payload_uses_final_onboarding_substep_mapping(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "profile",
                    "onboarding_substep": "profile_collect",
                }
            }
        },
        profile_fields={"first_name": "Ada", "last_name": "Lovelace", "birth_date": "1815-12-10"},
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post(
        "/agent/chat",
        json={"message": "bonjour"},
        headers={**_auth_headers(), "X-Debug": "1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["debug"]["loop"] == {"loop_id": "onboarding.profile_confirm", "step": "start", "blocking": True}


def test_agent_chat_debug_payload_includes_null_loop_when_absent(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(initial_chat_state={"state": {"global_state": {"mode": "free_chat", "onboarding_step": None, "onboarding_substep": None, "has_imported_transactions": True}}})
    repo.bank_accounts = [{"id": "bank-1", "name": "UBS"}]
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post(
        "/agent/chat",
        json={"message": "peut-être"},
        headers={**_auth_headers(), "X-Debug": "1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["debug"]["loop"] == {"loop_id": None, "step": None, "blocking": None}


def test_onboarding_substep_exposes_virtual_loop_debug_without_persisting(monkeypatch) -> None:
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
        }
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post(
        "/agent/chat",
        json={"message": "peut-être"},
        headers={**_auth_headers(), "X-Debug": "1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["debug"]["loop"]["loop_id"] == "onboarding.profile_confirm"
    assert payload["debug"]["loop"]["step"] == "start"
    assert payload["debug"]["loop"]["blocking"] is True
    assert repo.update_calls == []
