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
    assert response.json()["reply"] == "Parfait. Quel compte veux-tu importer ?"
    assert "nom exact" not in response.json()["reply"].lower()
    assert "Voulez-vous créer encore d'autres comptes" not in response.json()["reply"]
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_step"] == "import"
    assert persisted["onboarding_substep"] == "import_select_account"
    assert persisted["bank_accounts_confirmed"] is True
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
    assert payload["tool_result"]["bank_account_id"] == "bank-1"
    assert "envoie ton fichier" in payload["reply"].lower()
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
    assert "Import terminé" in payload["reply"]
    assert "je reconnais d’abord" in payload["reply"].lower()
    assert "Marchands classés" in payload["reply"]
    assert "Réponds 1 ou 2" in payload["reply"]
    assert len(repo.profile_categories) == 10
    assert repo.merchants[0]["category"] == "Alimentation"
    assert repo.merchants[1]["category"] == "Autres"
    assert repo.merchants[2]["category"] == ""
    assert "Marchands classés : 2/3" in payload["reply"]
    assert "transactions mises à jour" not in payload["reply"]
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_step"] == "categories"
    assert persisted["onboarding_substep"] == "categories_review"
    assert loop.called is False


def test_categories_review_requires_choice_1_or_2(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "categories",
                    "onboarding_substep": "categories_review",
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
    loop = _LoopSpy()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "OUI"}, headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()["reply"] == "Réponds 1 ou 2."
    assert repo.update_calls == []
    assert loop.called is False




def test_categories_review_choice_1_runs_more_classification(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "categories",
                    "onboarding_substep": "categories_review",
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
        {"id": UUID("11111111-1111-1111-1111-111111111111"), "name_norm": "migros rive", "name": "Migros Rive", "category": None},
        {"id": "not-a-uuid", "name_norm": "broken", "name": "Broken", "category": ""},
    ]
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "1"}, headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert repo.merchants[0]["category"] == "Alimentation"
    assert "classés" in payload["reply"].lower()
    assert "reste" in payload["reply"].lower() or "restants" in payload["reply"].lower()

def test_categories_review_non_switches_to_free_chat_without_loop(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "categories",
                    "onboarding_substep": "categories_review",
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
    loop = _LoopSpy()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "NON"}, headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"] == "Réponds 1 ou 2."
    assert repo.update_calls == []
    assert loop.called is False



def test_report_offer_oui_returns_pdf_ui_request_and_switches_to_free_chat(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "last_query": {"month": "2026-01"},
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "report",
                    "onboarding_substep": "report_offer",
                    "profile_confirmed": True,
                    "bank_accounts_confirmed": True,
                    "has_bank_accounts": True,
                    "has_imported_transactions": True,
                    "budget_created": False,
                },
            }
        },
    )
    loop = _LoopSpy()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post("/agent/chat", json={"message": "OUI"}, headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["tool_result"]["type"] == "ui_request"
    assert payload["tool_result"]["name"] == "open_pdf_report"
    assert "month=2026-01" in payload["tool_result"]["url"]
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["mode"] == "free_chat"
    assert persisted["onboarding_step"] is None
    assert persisted["onboarding_substep"] is None
    assert loop.called is False


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
    assert response.json()["reply"] == "Avant de continuer, tu dois importer un relevé. Quel compte veux-tu importer ?"
    persisted_state = repo.update_calls[-1]["chat_state"]["state"]
    assert persisted_state["global_state"]["onboarding_step"] == "import"
    assert persisted_state["global_state"]["onboarding_substep"] == "import_select_account"
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
                    "onboarding_substep": "profile_collect",
                }
            }
        },
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "Salut"}, headers=_auth_headers())

    assert response.status_code == 200
    assert "prénom" in response.json()["reply"].lower()


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
    assert persisted["onboarding_substep"] == "profile_collect"
    assert "Avant de continuer" in response.json()["reply"]
    assert "prénom" in response.json()["reply"]


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
    assert "prénom" in reply
    assert "nom" in reply
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
    assert persisted["onboarding_substep"] == "profile_collect"


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
    assert persisted["onboarding_substep"] == "profile_collect"
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
    assert "Avant de continuer" in response.json()["reply"]


def test_onboarding_profile_name_message_updates_first_and_last_name(monkeypatch) -> None:
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
    assert repo.profile_update_calls == [{"first_name": "Tristan", "last_name": "Pfefferlé"}]
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_substep"] == "profile_collect"
    assert "date de naissance" in response.json()["reply"].lower()
    assert "date de naissance" in response.json()["reply"]
    assert loop.called is False


def test_onboarding_profile_name_message_acknowledges_and_asks_birth_date(monkeypatch) -> None:
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
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "Paul Gorok"}, headers=_auth_headers())

    assert response.status_code == 200
    assert {"first_name": "Paul", "last_name": "Gorok"} in repo.profile_update_calls
    assert "date de naissance" in response.json()["reply"].lower()
    assert "date de naissance" in response.json()["reply"]
    assert "Pour démarrer, j’ai besoin" not in response.json()["reply"]


def test_onboarding_profile_combined_name_and_birth_date_in_one_message_moves_to_confirm(monkeypatch) -> None:
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
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "Paul Gorok 10.01.1994"}, headers=_auth_headers())

    assert response.status_code == 200
    assert {"first_name": "Paul", "last_name": "Gorok"} in repo.profile_update_calls
    assert {"birth_date": "1994-01-10"} in repo.profile_update_calls
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_substep"] == "bank_accounts_collect"
    assert "Parfait" in response.json()["reply"]
    assert "comptes bancaires" in response.json()["reply"].lower()
    assert "Confirmez-vous" not in response.json()["reply"]


@pytest.mark.parametrize(
    ("message", "expected_birth_date"),
    [
        ("1992-01-15", "1992-01-15"),
        ("12.01.2002", "2002-01-12"),
        ("14 janvier 2002", "2002-01-14"),
    ],
)
def test_onboarding_profile_birth_date_message_promotes_to_bank_accounts_step(monkeypatch, message: str, expected_birth_date: str) -> None:
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

    response = client.post("/agent/chat", json={"message": message}, headers=_auth_headers())

    assert response.status_code == 200
    assert {"birth_date": expected_birth_date} in repo.profile_update_calls
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_step"] == "bank_accounts"
    assert persisted["onboarding_substep"] == "bank_accounts_collect"


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
    assert "prénom" in response.json()["reply"].lower()
    assert loop.called is False


def test_promotes_to_bank_accounts_onboarding_when_profile_becomes_complete(monkeypatch) -> None:
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
    persisted_collect = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted_collect["onboarding_substep"] == "bank_accounts_collect"

    

def test_onboarding_bank_accounts_unrecognized_input_returns_help_without_creation(monkeypatch) -> None:
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
    assert repo.ensure_bank_accounts_calls == []
    assert (
        "Je n’ai pas reconnu" in response.json()["reply"]
        or "Indique-moi tes banques" in response.json()["reply"]
    )


def test_onboarding_bank_accounts_request_like_input_returns_blocking_help(monkeypatch) -> None:
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

    response = client.post("/agent/chat", json={"message": "Liste mes catégories"}, headers=_auth_headers())

    assert response.status_code == 200
    assert repo.ensure_bank_accounts_calls == []
    assert "Avant de continuer" in response.json()["reply"]


def test_onboarding_bank_accounts_creates_accounts_and_moves_to_import(monkeypatch) -> None:
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

    response = client.post("/agent/chat", json={"message": "UBS et Revolut"}, headers=_auth_headers())

    assert response.status_code == 200
    assert repo.ensure_bank_accounts_calls == [{"names": ["UBS", "Revolut"]}]
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_substep"] == "bank_accounts_confirm"
    assert "autre" in response.json()["reply"].lower()
    assert "import" in response.json()["reply"].lower()


def test_onboarding_bank_accounts_skips_creation_if_already_exists(monkeypatch) -> None:
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
    repo.bank_accounts = [{"id": "bank-1", "name": "UBS"}]
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "Salut"}, headers=_auth_headers())

    assert response.status_code == 200
    assert repo.ensure_bank_accounts_calls == []
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_substep"] == "bank_accounts_confirm"
    assert "autre" in response.json()["reply"].lower()
    assert "import" in response.json()["reply"].lower()




def test_onboarding_bank_accounts_collect_non_with_existing_accounts_moves_to_import_select(monkeypatch) -> None:
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
    repo.bank_accounts = [{"id": "bank-1", "name": "UBS"}]
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "non"}, headers=_auth_headers())

    assert response.status_code == 200
    assert repo.ensure_bank_accounts_calls == []
    persisted = repo.update_calls[-1]["chat_state"]["state"]["global_state"]
    assert persisted["onboarding_step"] == "import"
    assert persisted["onboarding_substep"] == "import_select_account"
    assert persisted["bank_accounts_confirmed"] is True
    assert persisted["has_bank_accounts"] is True
    assert "nom exact" not in response.json()["reply"].lower()


def test_onboarding_bank_accounts_collect_non_without_accounts_requires_bank(monkeypatch) -> None:
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

    response = client.post("/agent/chat", json={"message": "non"}, headers=_auth_headers())

    assert response.status_code == 200
    assert repo.ensure_bank_accounts_calls == []
    assert "au moins une banque" in response.json()["reply"].lower()

def test_onboarding_bank_accounts_canonicalizes_raifeisen(monkeypatch) -> None:
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
    monkeypatch.setattr(
        agent_api,
        "extract_canonical_banks",
        lambda message: (["Raiffeisen"], []) if "Raifeisen" in message else ([], [message]),
    )
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

    response = client.post("/agent/chat", json={"message": "Raifeisen"}, headers=_auth_headers())

    assert response.status_code == 200
    assert repo.ensure_bank_accounts_calls == [{"names": ["Raiffeisen"]}]


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



def test_onboarding_request_greeting_returns_intro_without_user_message(monkeypatch) -> None:
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
    loop = _LoopSpy()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: loop)

    response = client.post(
        "/agent/chat",
        json={"message": "", "request_greeting": True},
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert "salut" in response.json()["reply"].lower()
    assert "profil" in response.json()["reply"].lower()
    assert "import" in response.json()["reply"].lower()
    assert loop.called is False


def test_profile_collect_name_then_birth_date_skips_confirmation(monkeypatch) -> None:
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

    first = client.post("/agent/chat", json={"message": "Paul Mart"}, headers=_auth_headers())
    second = client.post("/agent/chat", json={"message": "10.01.2002"}, headers=_auth_headers())

    assert first.status_code == 200
    assert "date de naissance" in first.json()["reply"].lower()
    assert second.status_code == 200
    assert "parfait" in second.json()["reply"].lower()
    assert "comptes bancaires" in second.json()["reply"].lower()
    assert "confirmez-vous" not in second.json()["reply"].lower()



def test_categories_review_choice_2_returns_report_link(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    repo = _Repo(
        initial_chat_state={
            "state": {
                "global_state": {
                    "mode": "onboarding",
                    "onboarding_step": "categories",
                    "onboarding_substep": "categories_review",
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

    response = client.post("/agent/chat", json={"message": "2"}, headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert "[Ouvrir le PDF]" in payload["reply"]
    assert "Super, on peut s’arrêter ici" in payload["reply"]
    assert payload["tool_result"]["name"] == "open_pdf_report"
