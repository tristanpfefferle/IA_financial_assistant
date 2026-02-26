"""Tests for finance import endpoints exposed by agent.api."""

import base64
import re
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi.testclient import TestClient

import agent.api as agent_api
from agent.api import app
from backend.repositories.releves_repository import SupabaseRelevesRepository
from backend.repositories.shared_expenses_repository import InMemorySharedExpensesRepository, SharedExpenseRow
from shared.models import ToolError, ToolErrorCode


client = TestClient(app)
AUTH_USER_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def _mock_authenticated(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )

    class _Repo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return PROFILE_ID

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())


def test_list_bank_accounts_returns_router_payload(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _Router:
        def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
            assert tool_name == "finance_bank_accounts_list"
            assert payload == {}
            assert profile_id == PROFILE_ID
            return {"items": [{"id": str(UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")), "name": "UBS"}]}

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    response = client.get("/finance/bank-accounts", headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()["items"][0]["name"] == "UBS"


def test_list_bank_accounts_maps_tool_error_to_http_400(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _Router:
        def call(self, _tool_name: str, _payload: dict, *, profile_id: UUID | None = None):
            assert profile_id == PROFILE_ID
            return ToolError(code=ToolErrorCode.VALIDATION_ERROR, message="invalid")

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    response = client.get("/finance/bank-accounts", headers=_auth_headers())

    assert response.status_code == 400
    assert response.json()["detail"] == "invalid"


def test_import_releves_returns_router_payload(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _Router:
        def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
            assert profile_id == PROFILE_ID
            if tool_name == "finance_releves_import_files":
                assert payload["import_mode"] == "analyze"
                assert payload["modified_action"] == "replace"
                assert payload["bank_account_id"] == str(UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"))
                assert payload["files"][0]["filename"] == "ubs.csv"
                return {
                    "imported_count": 2,
                    "requires_confirmation": True,
                    "preview": [
                        {"date": "2025-01-02", "montant": "10", "devise": "CHF"},
                        {"date": "2025-01-30", "montant": "20", "devise": "CHF"},
                    ],
                }
            assert tool_name == "finance_bank_accounts_list"
            assert payload == {}
            return {"items": [{"id": str(UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")), "name": "UBS"}]}

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    response = client.post(
        "/finance/releves/import",
        headers=_auth_headers(),
        json={
            "files": [{"filename": "ubs.csv", "content_base64": "YQ=="}],
            "bank_account_id": str(UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")),
            "import_mode": "analyze",
            "modified_action": "replace",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["requires_confirmation"] is True
    assert payload["ok"] is True
    assert payload["transactions_imported_count"] == 2
    assert payload["date_range"] == {"start": "2025-01-02", "end": "2025-01-30"}
    assert payload["bank_account_name"] == "UBS"


def test_import_releves_maps_tool_error_to_http_400(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _Router:
        def call(self, _tool_name: str, _payload: dict, *, profile_id: UUID | None = None):
            assert profile_id == PROFILE_ID
            return ToolError(
                code=ToolErrorCode.VALIDATION_ERROR,
                message="invalid import",
                details={"file": "ubs.csv"},
            )

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    response = client.post(
        "/finance/releves/import",
        headers=_auth_headers(),
        json={"files": [{"filename": "ubs.csv", "content_base64": "YQ=="}]},
    )

    assert response.status_code == 400
    assert "invalid import" in response.json()["detail"]


def test_import_releves_without_bank_account_uses_single_existing_account(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _Repo:
        def __init__(self) -> None:
            self.last_chat_state = None

        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return PROFILE_ID

        def list_bank_accounts(self, *, profile_id: UUID):
            assert profile_id == PROFILE_ID
            return [{"id": str(UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")), "name": "UBS"}]

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            return {}

        def update_chat_state(self, *, profile_id: UUID, user_id: UUID, chat_state: dict):
            self.last_chat_state = chat_state

        def list_releves_without_merchant(self, *, profile_id: UUID, limit: int = 500):
            return []

    repo = _Repo()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)

    captured_import_payload: dict[str, object] = {}

    class _Router:
        def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
            assert profile_id == PROFILE_ID
            if tool_name == "finance_releves_import_files":
                captured_import_payload.update(payload)
                return {"imported_count": 1}
            if tool_name == "finance_bank_accounts_list":
                return {"items": [{"id": str(UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")), "name": "UBS"}]}
            return {"items": []}

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    response = client.post(
        "/finance/releves/import",
        headers=_auth_headers(),
        json={"files": [{"filename": "statement.csv", "content_base64": "YQ=="}]},
    )

    assert response.status_code == 200
    assert captured_import_payload["bank_account_id"] == str(UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"))
    assert response.json()["bank_account_name"] == "UBS"


def test_import_releves_without_bank_account_matches_filename(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _Repo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            return PROFILE_ID

        def list_bank_accounts(self, *, profile_id: UUID):
            return [
                {"id": str(UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")), "name": "UBS"},
                {"id": str(UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")), "name": "Revolut"},
            ]

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            return {}

        def update_chat_state(self, *, profile_id: UUID, user_id: UUID, chat_state: dict):
            return None

        def list_releves_without_merchant(self, *, profile_id: UUID, limit: int = 500):
            return []

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())

    captured_import_payload: dict[str, object] = {}

    class _Router:
        def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
            if tool_name == "finance_releves_import_files":
                captured_import_payload.update(payload)
                return {"imported_count": 1}
            if tool_name == "finance_bank_accounts_list":
                return {"items": [{"id": str(UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")), "name": "UBS"}]}
            return {"items": []}

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    response = client.post(
        "/finance/releves/import",
        headers=_auth_headers(),
        json={"files": [{"filename": "Releve_UBS_2025.csv", "content_base64": "YQ=="}]},
    )

    assert response.status_code == 200
    assert captured_import_payload["bank_account_id"] == str(UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"))


def test_import_releves_without_bank_account_returns_clarification_when_ambiguous(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _Repo:
        def __init__(self) -> None:
            self.last_chat_state: dict | None = None

        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            return PROFILE_ID

        def list_bank_accounts(self, *, profile_id: UUID):
            return [
                {"id": str(UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")), "name": "UBS"},
                {"id": str(UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")), "name": "Revolut"},
            ]

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            return {"state": {}}

        def update_chat_state(self, *, profile_id: UUID, user_id: UUID, chat_state: dict):
            self.last_chat_state = chat_state

    repo = _Repo()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)

    class _Router:
        def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
            if tool_name == "finance_releves_import_files":
                raise AssertionError("import tool should not be called in ambiguous mode")
            return {"items": []}

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    response = client.post(
        "/finance/releves/import",
        headers=_auth_headers(),
        json={"files": [{"filename": "statement.csv", "content_base64": "YQ=="}]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["type"] == "clarification"
    assert "UBS" in payload["message"] and "Revolut" in payload["message"]
    assert repo.last_chat_state is not None
    pending_files = repo.last_chat_state["state"]["import_context"]["pending_files"]
    assert pending_files == [{"filename": "statement.csv", "content_base64": "YQ=="}]


def test_import_releves_without_bank_account_auto_selects_from_csv_structure(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _Repo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            return PROFILE_ID

        def list_bank_accounts(self, *, profile_id: UUID):
            return [
                {"id": str(UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")), "name": "UBS"},
                {"id": str(UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")), "name": "Revolut"},
            ]

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            return {}

        def update_chat_state(self, *, profile_id: UUID, user_id: UUID, chat_state: dict):
            raise AssertionError("clarification path should not persist chat state")

        def list_releves_without_merchant(self, *, profile_id: UUID, limit: int = 500):
            return []

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())

    captured_import_payload: dict[str, object] = {}

    class _Router:
        def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
            if tool_name == "finance_releves_import_files":
                captured_import_payload.update(payload)
                return {"imported_count": 1}
            if tool_name == "finance_bank_accounts_list":
                return {
                    "items": [
                        {"id": str(UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")), "name": "UBS"},
                        {"id": str(UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")), "name": "Revolut"},
                    ]
                }
            return {"items": []}

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    revolut_csv = (
        "Type,Product,Started Date,Completed Date,Description,Amount,Fee,Currency,State,Balance\n"
        "CARD,CURRENT,2025-01-01,2025-01-01,Coffee,-4.20,0.00,CHF,COMPLETED,100.00\n"
    )
    response = client.post(
        "/finance/releves/import",
        headers=_auth_headers(),
        json={
            "files": [
                {
                    "filename": "statement.csv",
                    "content_base64": base64.b64encode(revolut_csv.encode("utf-8")).decode("ascii"),
                }
            ]
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload.get("type") != "clarification"
    assert captured_import_payload["bank_account_id"] == str(UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"))
    assert payload["bank_account_name"] == "Revolut"




def test_import_releves_rejects_non_csv_files_before_router_call(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _Router:
        def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
            raise AssertionError(f"router should not be called for invalid file type ({tool_name})")

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    response = client.post(
        "/finance/releves/import",
        headers=_auth_headers(),
        json={
            "files": [
                {
                    "filename": "statement.pdf",
                    "content_base64": base64.b64encode(b"%PDF-1.4\n").decode("ascii"),
                }
            ]
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["type"] == "error"
    assert payload["error"]["code"] == "invalid_file_type"
    assert payload["message"] == "Format invalide. Pour l’instant, seul le format CSV est supporté."


def test_import_releves_updates_chat_state_after_success(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )

    class _Repo:
        def __init__(self) -> None:
            self.last_chat_state = None
            self.link_calls: list[tuple[UUID, UUID]] = []

        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return PROFILE_ID

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            assert profile_id == PROFILE_ID
            assert user_id == AUTH_USER_ID
            return {
                "state": {
                    "global_state": {
                        "mode": "onboarding",
                        "onboarding_step": "import",
                        "onboarding_substep": "import_select_account",
                        "profile_confirmed": True,
                        "bank_accounts_confirmed": True,
                        "has_bank_accounts": True,
                        "has_imported_transactions": False,
                        "budget_created": False,
                    },
                    "import_context": {
                        "selected_bank_account_id": str(UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")),
                        "selected_bank_account_name": "UBS",
                    },
                }
            }

        def update_chat_state(self, *, profile_id: UUID, user_id: UUID, chat_state: dict):
            assert profile_id == PROFILE_ID
            assert user_id == AUTH_USER_ID
            self.last_chat_state = chat_state

        def list_releves_without_merchant(self, *, profile_id: UUID, limit: int = 500):
            assert profile_id == PROFILE_ID
            assert limit == 500
            return []

        def upsert_merchant_by_name_norm(self, *, profile_id: UUID, name: str, name_norm: str, scope: str = "personal"):
            assert profile_id == PROFILE_ID
            assert scope == "personal"
            return UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")

        def attach_merchant_to_releve(self, *, releve_id: UUID, merchant_id: UUID) -> None:
            self.link_calls.append((releve_id, merchant_id))

    repo = _Repo()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)

    class _Router:
        def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
            assert profile_id == PROFILE_ID
            if tool_name == "finance_releves_import_files":
                return {"imported_count": 1, "preview": [{"date": "2025-01-05"}]}
            assert tool_name == "finance_bank_accounts_list"
            assert payload == {}
            return {"items": [{"id": str(UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")), "name": "UBS"}]}

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    response = client.post(
        "/finance/releves/import",
        headers=_auth_headers(),
        json={
            "files": [{"filename": "ubs.csv", "content_base64": "YQ=="}],
            "bank_account_id": str(UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")),
        },
    )

    assert response.status_code == 200
    assert repo.last_chat_state is not None
    assert repo.last_chat_state["state"]["global_state"]["onboarding_step"] == "categories"
    assert repo.last_chat_state["state"]["global_state"]["onboarding_substep"] == "categories_bootstrap"
    assert repo.last_chat_state["state"]["global_state"]["has_imported_transactions"] is True
    assert "import_context" not in repo.last_chat_state["state"]


def test_import_releves_keeps_success_when_chat_state_update_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )

    class _Repo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return PROFILE_ID

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            assert profile_id == PROFILE_ID
            assert user_id == AUTH_USER_ID
            return {}

        def update_chat_state(self, *, profile_id: UUID, user_id: UUID, chat_state: dict):
            raise RuntimeError("boom")

        def list_releves_without_merchant(self, *, profile_id: UUID, limit: int = 500):
            assert profile_id == PROFILE_ID
            assert limit == 500
            return []

        def upsert_merchant_by_name_norm(self, *, profile_id: UUID, name: str, name_norm: str, scope: str = "personal"):
            assert profile_id == PROFILE_ID
            return UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")

        def attach_merchant_to_releve(self, *, releve_id: UUID, merchant_id: UUID) -> None:
            return None

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())

    class _Router:
        def call(self, tool_name: str, _payload: dict, *, profile_id: UUID | None = None):
            assert profile_id == PROFILE_ID
            if tool_name == "finance_releves_import_files":
                return {"imported_count": 1}
            return {"items": []}

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    response = client.post(
        "/finance/releves/import",
        headers=_auth_headers(),
        json={"files": [{"filename": "ubs.csv", "content_base64": "YQ=="}]},
    )

    assert response.status_code == 200
    warnings = response.json().get("warnings")
    assert isinstance(warnings, list)
    assert "chat_state_update_failed" in warnings


def test_import_releves_links_merchants_from_imported_transactions(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )

    class _Repo:
        def __init__(self) -> None:
            self.ensure_system_categories_calls = 0
            self.attach_calls: list[tuple[UUID, UUID, UUID | None]] = []
            self.alias_calls: list[tuple[UUID, str, str]] = []
            self.override_calls: list[tuple[UUID, UUID, UUID | None, str]] = []
            self._categories: list[dict[str, str]] = []

        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return PROFILE_ID

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            assert profile_id == PROFILE_ID
            assert user_id == AUTH_USER_ID
            return {}

        def update_chat_state(self, *, profile_id: UUID, user_id: UUID, chat_state: dict):
            assert profile_id == PROFILE_ID
            assert user_id == AUTH_USER_ID

        def ensure_system_categories(self, *, profile_id: UUID, categories: list[dict[str, str]]) -> dict[str, int]:
            assert profile_id == PROFILE_ID
            self.ensure_system_categories_calls += 1
            self._categories = [
                {
                    "id": str(UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")),
                    "system_key": "food",
                    "name_norm": "alimentation",
                }
            ]
            assert any(category.get("system_key") == "food" for category in categories)
            return {"created_count": 1, "system_total_count": len(self._categories)}

        def list_releves_without_merchant(self, *, profile_id: UUID, limit: int = 500):
            assert profile_id == PROFILE_ID
            assert limit == 500
            return [
                {
                    "id": str(UUID("11111111-1111-1111-1111-111111111111")),
                    "payee": "COOP-4815 MONTHEY; Paiement UBS TWINT Motif du paiement: ...",
                    "libelle": "",
                    "created_at": None,
                    "date": "2025-01-01",
                },
                {
                    "id": str(UUID("22222222-2222-2222-2222-222222222222")),
                    "payee": "",
                    "libelle": "SBB MOBILE; Paiement UBS TWINT ...",
                    "created_at": None,
                    "date": "2025-01-02",
                },
            ]

        def list_profile_categories(self, *, profile_id: UUID):
            return self._categories

        def find_merchant_entity_by_alias_norm(self, *, alias_norm: str):
            if "coop" in alias_norm:
                return {
                    "id": str(UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")),
                    "suggested_category_norm": "food",
                    "suggested_category_label": "Alimentation",
                }
            if "sbb" in alias_norm:
                return {
                    "id": str(UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")),
                    "suggested_category_norm": "food",
                    "suggested_category_label": "Alimentation",
                }
            return None

        def get_profile_merchant_override(self, *, profile_id: UUID, merchant_entity_id: UUID):
            return None

        def attach_merchant_entity_to_releve(
            self,
            *,
            releve_id: UUID,
            merchant_entity_id: UUID,
            category_id: UUID | None,
        ) -> None:
            self.attach_calls.append((releve_id, merchant_entity_id, category_id))

        def upsert_merchant_alias(
            self,
            *,
            merchant_entity_id: UUID,
            alias: str,
            alias_norm: str,
            source: str = "import",
        ) -> None:
            self.alias_calls.append((merchant_entity_id, alias, alias_norm))

        def upsert_profile_merchant_override(
            self,
            *,
            profile_id: UUID,
            merchant_entity_id: UUID,
            category_id: UUID | None,
            status: str = "auto",
        ) -> None:
            self.override_calls.append((profile_id, merchant_entity_id, category_id, status))

    repo = _Repo()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)

    class _Router:
        def call(self, tool_name: str, _payload: dict, *, profile_id: UUID | None = None):
            assert profile_id == PROFILE_ID
            if tool_name == "finance_releves_import_files":
                return {"imported_count": 2}
            return {"items": []}

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    response = client.post(
        "/finance/releves/import",
        headers=_auth_headers(),
        json={"files": [{"filename": "ubs.csv", "content_base64": "YQ=="}]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["merchant_suggestions_created_count"] == 0
    assert repo.ensure_system_categories_calls == 1
    assert len(repo.attach_calls) == 2
    assert repo.attach_calls == [
        (
            UUID("11111111-1111-1111-1111-111111111111"),
            UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
            UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"),
        ),
        (
            UUID("22222222-2222-2222-2222-222222222222"),
            UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
            UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"),
        ),
    ]
    assert repo.alias_calls == [
        (
            UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
            "Coop",
            "coop",
        ),
        (
            UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
            "SBB Mobile",
            "sbb mobile",
        ),
    ]
    assert len(repo.override_calls) == 2


def test_rename_merchant_endpoint_returns_200_and_calls_repo(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )

    merchant_id = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")

    class _Repo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return PROFILE_ID

        def rename_merchant(self, *, profile_id: UUID, merchant_id: UUID, new_name: str):
            assert profile_id == PROFILE_ID
            assert merchant_id == UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
            assert new_name == "Nouveau Marchand"
            return {"merchant_id": str(merchant_id), "name": "Nouveau Marchand", "name_norm": "nouveau marchand"}

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())

    response = client.post(
        "/finance/merchants/rename",
        headers=_auth_headers(),
        json={"merchant_id": str(merchant_id), "name": "Nouveau Marchand"},
    )

    assert response.status_code == 200
    assert response.json()["name_norm"] == "nouveau marchand"


def test_merge_merchants_endpoint_returns_200_and_calls_repo(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )

    source_merchant_id = UUID("11111111-1111-1111-1111-111111111111")
    target_merchant_id = UUID("22222222-2222-2222-2222-222222222222")

    class _Repo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return PROFILE_ID

        def merge_merchants(self, *, profile_id: UUID, source_merchant_id: UUID, target_merchant_id: UUID):
            assert profile_id == PROFILE_ID
            assert source_merchant_id == UUID("11111111-1111-1111-1111-111111111111")
            assert target_merchant_id == UUID("22222222-2222-2222-2222-222222222222")
            return {
                "target_merchant_id": str(target_merchant_id),
                "source_merchant_id": str(source_merchant_id),
                "moved_releves_count": 3,
                "aliases_added_count": 2,
                "target_aliases_count": 5,
            }

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())

    response = client.post(
        "/finance/merchants/merge",
        headers=_auth_headers(),
        json={
            "source_merchant_id": str(source_merchant_id),
            "target_merchant_id": str(target_merchant_id),
        },
    )

    assert response.status_code == 200
    assert response.json()["moved_releves_count"] == 3


def test_spending_report_pdf_accepts_access_token_query_param(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def _get_user(token: str):
        captured["token"] = token
        return {"id": str(AUTH_USER_ID), "email": "user@example.com"}

    monkeypatch.setattr(agent_api, "get_user_from_bearer_token", _get_user)

    class _Repo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return PROFILE_ID

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            assert profile_id == PROFILE_ID
            assert user_id == AUTH_USER_ID
            return {"state": {}}

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())

    class _Router:
        def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
            assert profile_id == PROFILE_ID
            if tool_name == "finance_releves_sum":
                return {"total": "0", "count": 0, "average": "0", "currency": "CHF"}
            if tool_name == "finance_releves_aggregate":
                return {"group_by": payload.get("group_by") or "month", "currency": "CHF", "groups": {}}
            if tool_name == "finance_releves_search":
                return {"items": [], "limit": 500, "offset": 0, "total": 0}
            raise AssertionError(tool_name)

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    response = client.get("/finance/reports/spending.pdf?month=2026-01&access_token=query-token")

    assert response.status_code == 200
    assert captured["token"] == "query-token"



def test_spending_report_pdf_internal_error_returns_error_id_and_debug_fields(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _Repo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return PROFILE_ID

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            assert profile_id == PROFILE_ID
            assert user_id == AUTH_USER_ID
            return {"state": {}}

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())

    class _Router:
        def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
            assert profile_id == PROFILE_ID
            if tool_name == "finance_releves_sum":
                return {"total": "0", "count": 0, "average": "0", "currency": "CHF"}
            if tool_name == "finance_releves_aggregate":
                return {"group_by": payload.get("group_by") or "month", "currency": "CHF", "groups": {}}
            if tool_name == "finance_releves_search":
                return {"items": [], "limit": 500, "offset": 0, "total": 0}
            raise AssertionError(tool_name)

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    def _raise_pdf_error(_data):
        raise RuntimeError("pdf generation exploded")

    monkeypatch.setattr(agent_api, "generate_spending_report_pdf", _raise_pdf_error)

    no_debug_client = TestClient(app, raise_server_exceptions=False)

    response = no_debug_client.get("/finance/reports/spending.pdf?month=2026-01", headers=_auth_headers())

    assert response.status_code == 500
    payload = response.json()
    assert payload["detail"] == "Internal Server Error"
    assert payload["error_id"]
    assert "exception_type" not in payload
    assert "exception_message" not in payload
    assert response.headers.get("X-Error-Id") == payload["error_id"]

    debug_response = no_debug_client.get("/finance/reports/spending.pdf?month=2026-01&debug=1", headers=_auth_headers())

    assert debug_response.status_code == 500
    debug_payload = debug_response.json()
    assert debug_payload["detail"] == "Internal Server Error"
    assert debug_payload["error_id"]
    assert debug_payload["exception_type"] == "RuntimeError"
    assert debug_payload["exception_message"] == "pdf generation exploded"
    assert debug_response.headers.get("X-Error-Id") == debug_payload["error_id"]

def test_spending_report_pdf_returns_pdf_two_pages_and_calls_search(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _Repo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return PROFILE_ID

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            assert profile_id == PROFILE_ID
            assert user_id == AUTH_USER_ID
            return {"state": {"last_query": {"month": "2026-01"}}}

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())

    class _Router:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
            assert profile_id == PROFILE_ID
            self.calls.append((tool_name, payload))
            if tool_name == "finance_releves_sum":
                return {"total": "-120.50", "count": 3, "average": "-40.166", "currency": "CHF"}
            if tool_name == "finance_releves_aggregate":
                if payload.get("group_by") == "categorie":
                    return {
                        "group_by": "categorie",
                        "currency": "CHF",
                        "groups": {
                            "Alimentation": {"total": "-80", "count": 2},
                            "Transport": {"total": "-40.5", "count": 1},
                        },
                    }
                return {"group_by": "month", "currency": "CHF", "groups": {"2026-01": {"total": "-120.50", "count": 3}}}
            if tool_name == "finance_releves_search":
                assert payload["date_range"] == {"start_date": "2026-01-01", "end_date": "2026-01-31"}
                assert "direction" not in payload
                assert payload["include_internal_transfers"] is True
                assert payload["limit"] == 500
                assert payload["offset"] == 0
                return {
                    "items": [
                        {"date": "2026-01-05", "montant": "-80", "devise": "CHF", "payee": "Migros", "categorie": "Alimentation"},
                        {"date": "2026-01-10", "montant": "-40.5", "devise": "CHF", "libelle": "SBB", "categorie": "Transport"},
                    ],
                    "limit": 500,
                    "offset": 0,
                    "total": 2,
                }
            raise AssertionError(tool_name)

    router = _Router()
    monkeypatch.setattr(agent_api, "get_tool_router", lambda: router)

    response = client.get("/finance/reports/spending.pdf?month=2026-01", headers=_auth_headers())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF")
    assert len(re.findall(rb"/Type /Page\b", response.content)) >= 2

    sum_calls = [payload for tool_name, payload in router.calls if tool_name == "finance_releves_sum"]
    assert len(sum_calls) == 1
    assert sum_calls[0]["date_range"]["start_date"] == "2026-01-01"
    assert sum_calls[0]["date_range"]["end_date"] == "2026-01-31"

    aggregate_calls = [payload for tool_name, payload in router.calls if tool_name == "finance_releves_aggregate"]
    assert len(aggregate_calls) == 1
    assert aggregate_calls[0]["group_by"] == "categorie"

    search_calls = [payload for tool_name, payload in router.calls if tool_name == "finance_releves_search"]
    assert len(search_calls) == 1
    assert search_calls[0]["date_range"] == {"start_date": "2026-01-01", "end_date": "2026-01-31"}
    assert search_calls[0]["include_internal_transfers"] is True
    assert "direction" not in search_calls[0]


def test_spending_report_pdf_uses_last_query_filters_date_range(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _Repo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return PROFILE_ID

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            assert profile_id == PROFILE_ID
            assert user_id == AUTH_USER_ID
            return {
                "state": {
                    "last_query": {
                        "filters": {
                            "date_range": {
                                "start_date": "2026-02-01",
                                "end_date": "2026-02-28",
                            }
                        }
                    }
                }
            }

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())

    class _Router:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
            assert profile_id == PROFILE_ID
            self.calls.append((tool_name, payload))
            if tool_name == "finance_releves_sum":
                return {"total": "0", "count": 0, "average": "0", "currency": "CHF"}
            if tool_name == "finance_releves_aggregate" and payload.get("group_by") == "categorie":
                return {"group_by": "categorie", "currency": "CHF", "groups": {}}
            if tool_name == "finance_releves_search":
                return {"items": [], "limit": 500, "offset": 0, "total": 0}
            raise AssertionError(tool_name)

    router = _Router()
    monkeypatch.setattr(agent_api, "get_tool_router", lambda: router)

    response = client.get("/finance/reports/spending.pdf", headers=_auth_headers())

    assert response.status_code == 200
    sum_calls = [payload for tool_name, payload in router.calls if tool_name == "finance_releves_sum"]
    assert len(sum_calls) == 1
    assert sum_calls[0]["date_range"] == {"start_date": "2026-02-01", "end_date": "2026-02-28"}


def test_spending_report_pdf_no_data_still_returns_pdf(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _Repo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return PROFILE_ID

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            assert profile_id == PROFILE_ID
            assert user_id == AUTH_USER_ID
            return {}

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())

    class _Router:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
            assert profile_id == PROFILE_ID
            self.calls.append((tool_name, payload))
            if tool_name == "finance_releves_sum":
                return {"total": "0", "count": 0, "average": "0", "currency": "CHF"}
            if tool_name == "finance_releves_aggregate" and payload.get("group_by") == "month":
                return {"group_by": "month", "currency": "CHF", "groups": {}}
            if tool_name == "finance_releves_aggregate" and payload.get("group_by") == "categorie":
                return {"group_by": "categorie", "currency": "CHF", "groups": {}}
            if tool_name == "finance_releves_search":
                return {"items": [], "limit": 500, "offset": 0, "total": 0}
            raise AssertionError(tool_name)

    router = _Router()
    monkeypatch.setattr(agent_api, "get_tool_router", lambda: router)

    response = client.get(
        "/finance/reports/spending.pdf?start_date=2026-01-01&end_date=2026-01-31",
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert len(response.content) > 100
    assert response.content.startswith(b"%PDF")
    assert len(re.findall(rb"/Type /Page\b", response.content)) >= 2

    sum_calls = [payload for tool_name, payload in router.calls if tool_name == "finance_releves_sum"]
    assert len(sum_calls) == 1
    assert sum_calls[0]["date_range"]["start_date"] == "2026-01-01"
    assert sum_calls[0]["date_range"]["end_date"] == "2026-01-31"


def test_spending_report_pdf_falls_back_to_list_when_search_tool_is_missing(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _Repo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return PROFILE_ID

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            assert profile_id == PROFILE_ID
            assert user_id == AUTH_USER_ID
            return {"state": {"last_query": {"month": "2026-01"}}}

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())

    class _Router:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
            assert profile_id == PROFILE_ID
            self.calls.append((tool_name, payload))
            if tool_name == "finance_releves_sum":
                return {"total": "-40", "count": 1, "average": "-40", "currency": "CHF"}
            if tool_name == "finance_releves_aggregate" and payload.get("group_by") == "categorie":
                return {
                    "group_by": "categorie",
                    "currency": "CHF",
                    "groups": {"Transport": {"total": "-40", "count": 1}},
                }
            if tool_name == "finance_releves_search":
                return ToolError(code=ToolErrorCode.UNKNOWN_TOOL, message="unknown")
            if tool_name == "finance_releves_list":
                return {"items": [], "limit": 500, "offset": 0, "total": 0}
            raise AssertionError(tool_name)

    router = _Router()
    monkeypatch.setattr(agent_api, "get_tool_router", lambda: router)

    response = client.get("/finance/reports/spending.pdf?month=2026-01", headers=_auth_headers())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF")

    called_tools = [tool_name for tool_name, _payload in router.calls]
    assert "finance_releves_search" in called_tools
    assert "finance_releves_list" in called_tools


def test_spending_report_pdf_survives_transactions_unavailable(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _Repo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return PROFILE_ID

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            assert profile_id == PROFILE_ID
            assert user_id == AUTH_USER_ID
            return {"state": {"last_query": {"month": "2026-01"}}}

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())

    captured: dict[str, object] = {}

    def _fake_generate(data):
        captured["transactions_unavailable"] = data.transactions_unavailable
        return b"%PDF-1.4\n%fake\n"

    monkeypatch.setattr(agent_api, "generate_spending_report_pdf", _fake_generate)

    class _Router:
        def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
            assert profile_id == PROFILE_ID
            if tool_name == "finance_releves_sum":
                return {"total": "-40", "count": 1, "average": "-40", "currency": "CHF"}
            if tool_name == "finance_releves_aggregate" and payload.get("group_by") == "categorie":
                return {
                    "group_by": "categorie",
                    "currency": "CHF",
                    "groups": {"Transport": {"total": "-40", "count": 1}},
                }
            if tool_name == "finance_releves_search":
                return ToolError(code=ToolErrorCode.BACKEND_ERROR, message="boom")
            raise AssertionError(tool_name)

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    response = client.get("/finance/reports/spending.pdf?month=2026-01", headers=_auth_headers())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF")
    assert captured["transactions_unavailable"] is True

def test_spending_report_pdf_normalizes_categories_and_transaction_rows(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _Repo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return PROFILE_ID

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            assert profile_id == PROFILE_ID
            assert user_id == AUTH_USER_ID
            return {"state": {"last_query": {"month": "2026-01"}}}

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())

    captured: dict[str, object] = {}

    def _fake_generate(data):
        captured["categories"] = [(row.name, str(row.amount)) for row in data.categories]
        captured["expenses"] = [
            (row.date, row.merchant, row.category, str(row.amount))
            for row in data.transactions
            if row.flow_type == "expense"
        ]
        captured["incomes"] = [
            (row.date, row.merchant, row.category, str(row.amount))
            for row in data.transactions
            if row.flow_type == "income"
        ]
        captured["transfers"] = [
            (row.date, row.merchant, row.category, str(row.amount))
            for row in data.transactions
            if row.flow_type == "transfer_internal"
        ]
        return b"%PDF-1.4\n%fake\n"

    monkeypatch.setattr(agent_api, "generate_spending_report_pdf", _fake_generate)

    class _Router:
        def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
            assert profile_id == PROFILE_ID
            if tool_name == "finance_releves_sum":
                return {"total": "-120", "count": 2, "average": "-60", "currency": "CHF"}
            if tool_name == "finance_releves_aggregate" and payload.get("group_by") == "categorie":
                return {
                    "group_by": "categorie",
                    "currency": "CHF",
                    "groups": {
                        "Sans catégorie": {"total": "-10", "count": 1},
                        "Autres": {"total": "-5", "count": 1},
                        "Alimentation": {"total": "-105", "count": 1},
                    },
                }
            if tool_name == "finance_releves_search":
                return {
                    "items": [
                        {
                            "date": "2026-01-11",
                            "montant": "-10",
                            "merchant_entity_name": "Marchand Premium",
                            "libelle": "Marchand Long; Paiement UBS TWINT Motif 123",
                            "category_name": "Alimentation",
                        },
                        {
                            "date": "2026-01-01",
                            "montant": "-5",
                            "merchant": "Aucun",
                            "categorie": "",
                            "category_name": "Transport",
                        },
                        {
                            "date": "2026-01-02",
                            "montant": "500",
                            "payee": "Crédit TWINT",
                            "categorie": "Revenus",
                        },
                        {
                            "date": "2026-01-04",
                            "montant": "120",
                            "merchant": "Inconnu",
                            "categorie": "Transferts internes",
                        },
                        {
                            "date": "2026-01-03",
                            "montant": "-120",
                            "categorie": "Transferts internes",
                        },
                    ],
                    "limit": 500,
                    "offset": 0,
                    "total": 2,
                }
            raise AssertionError(tool_name)

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    response = client.get("/finance/reports/spending.pdf?month=2026-01", headers=_auth_headers())

    assert response.status_code == 200
    assert captured["categories"] == [("Autres", "15"), ("Alimentation", "105")]
    assert captured["expenses"] == [
        ("2026-01-01", "Aucun", "Transport", "-5"),
        ("2026-01-11", "Marchand Premium", "Alimentation", "-10"),
    ]
    assert captured["incomes"] == [
        ("2026-01-02", "Crédit TWINT", "Revenus", "500"),
    ]
    assert captured["transfers"] == [
        ("2026-01-03", "Transfert interne", "Transferts internes", "-120"),
        ("2026-01-04", "Transfert interne", "Transferts internes", "120"),
    ]




def test_spending_report_pdf_uses_merchant_entity_canonical_name_with_fallback(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    merchant_entity_id = UUID("11111111-1111-1111-1111-111111111111")

    class _Repo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return PROFILE_ID

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            assert profile_id == PROFILE_ID
            assert user_id == AUTH_USER_ID
            return {"state": {"last_query": {"month": "2026-01"}}}

        def get_merchant_entity_canonical_names_by_ids(self, *, merchant_entity_ids: list[UUID]) -> dict[UUID, str]:
            assert merchant_entity_ids == [merchant_entity_id]
            return {merchant_entity_id: "Coop"}

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())

    captured: dict[str, object] = {}

    def _fake_generate(data):
        captured["expenses"] = [
            (row.date, row.merchant, row.category, str(row.amount))
            for row in data.transactions
            if row.flow_type == "expense"
        ]
        return b"%PDF-1.4\n%fake\n"

    monkeypatch.setattr(agent_api, "generate_spending_report_pdf", _fake_generate)

    class _Router:
        def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
            assert profile_id == PROFILE_ID
            if tool_name == "finance_releves_sum":
                return {"total": "-22", "count": 2, "average": "-11", "currency": "CHF"}
            if tool_name == "finance_releves_aggregate" and payload.get("group_by") == "categorie":
                return {
                    "group_by": "categorie",
                    "currency": "CHF",
                    "groups": {"Alimentation": {"total": "-22", "count": 2}},
                }
            if tool_name == "finance_releves_search":
                return {
                    "items": [
                        {
                            "date": "2026-01-01",
                            "montant": "-10",
                            "merchant_entity_id": str(merchant_entity_id),
                            "payee": "COOP-4815 MONTHEY",
                            "categorie": "Alimentation",
                        },
                        {
                            "date": "2026-01-02",
                            "montant": "-12",
                            "libelle": "COOP-4815 MONTHEY",
                            "categorie": "Alimentation",
                        },
                    ],
                    "limit": 500,
                    "offset": 0,
                    "total": 2,
                }
            raise AssertionError(tool_name)

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    response = client.get("/finance/reports/spending.pdf?month=2026-01", headers=_auth_headers())

    assert response.status_code == 200
    assert captured["expenses"] == [
        ("2026-01-01", "Coop", "Alimentation", "-10"),
        ("2026-01-02", "COOP-4815 MONTHEY", "Alimentation", "-12"),
    ]


def test_spending_report_pdf_cashflow_summary_counts_positive_internal_transfer(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _Repo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return PROFILE_ID

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            assert profile_id == PROFILE_ID
            assert user_id == AUTH_USER_ID
            return {"state": {"last_query": {"month": "2026-01"}}}

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())

    captured: dict[str, object] = {}

    def _fake_generate(data):
        captured["cashflow_income"] = str(data.cashflow_income)
        captured["cashflow_expense"] = str(data.cashflow_expense)
        captured["cashflow_internal_transfers"] = str(data.cashflow_internal_transfers)
        captured["cashflow_net"] = str(data.cashflow_net)
        captured["cashflow_net_including_transfers"] = str(data.cashflow_net_including_transfers)
        return b"%PDF-1.4\n%fake\n"

    monkeypatch.setattr(agent_api, "generate_spending_report_pdf", _fake_generate)

    class _RelevesClient:
        def get_rows(self, *, table, query, with_count, use_anon_key=False):
            assert table == "releves_bancaires"
            return [
                {"montant": 5000, "devise": "CHF", "categorie": "Transferts internes", "metadonnees": {}},
                {"montant": 100, "devise": "CHF", "metadonnees": {}},
                {"montant": -30, "devise": "CHF", "metadonnees": {}},
            ], 0

    releves_repository = SupabaseRelevesRepository(client=_RelevesClient())

    class _Router:
        def __init__(self) -> None:
            self.backend_client = type(
                "_BackendClientStub",
                (),
                {"tool_service": type("_ToolServiceStub", (), {"releves_repository": releves_repository})()},
            )()

        def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
            assert profile_id == PROFILE_ID
            if tool_name == "finance_releves_sum":
                return {"total": "-30", "count": 1, "average": "-30", "currency": "CHF"}
            if tool_name == "finance_releves_aggregate":
                return {"group_by": "categorie", "currency": "CHF", "groups": {"Transport": {"total": "-30", "count": 1}}}
            if tool_name == "finance_releves_search":
                return {"items": [], "limit": 500, "offset": 0, "total": 0}
            raise AssertionError(tool_name)

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    response = client.get("/finance/reports/spending.pdf?month=2026-01", headers=_auth_headers())

    assert response.status_code == 200
    assert captured == {
        "cashflow_income": "100",
        "cashflow_expense": "-30",
        "cashflow_internal_transfers": "5000",
        "cashflow_net": "70",
        "cashflow_net_including_transfers": "5070",
    }

def test_pending_transactions_endpoint_counts_twint_and_excludes_internal(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _RelevesRepo:
        def list_pending_categorization_releves(self, *, profile_id: UUID, limit: int = 50):
            assert profile_id == PROFILE_ID
            assert limit == 50
            return [
                {
                    "id": "1",
                    "date": "2025-01-02",
                    "montant": "-10.00",
                    "devise": "CHF",
                    "libelle": "TWINT Luc",
                    "payee": "Luc",
                    "categorie": "À catégoriser (TWINT)",
                    "meta": {"category_key": "twint_p2p_pending", "category_status": "pending"},
                },
                {
                    "id": "2",
                    "date": "2025-01-03",
                    "montant": "-40.00",
                    "devise": "CHF",
                    "libelle": "Virement",
                    "payee": "Épargne",
                    "categorie": "Transferts internes",
                    "meta": {"tx_kind": "transfer_internal", "category_status": "pending"},
                },
                {
                    "id": "3",
                    "date": "2025-01-04",
                    "montant": "-8.00",
                    "devise": "CHF",
                    "libelle": "Café",
                    "payee": "Café",
                    "categorie": "Alimentation",
                    "meta": {"category_key": "food", "category_status": "done"},
                },
            ]

    class _ToolService:
        releves_repository = _RelevesRepo()

    class _BackendClient:
        tool_service = _ToolService()

    class _Router:
        backend_client = _BackendClient()

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    response = client.get("/finance/transactions/pending", headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["count_twint_p2p_pending"] == 1
    assert payload["count_total"] == 1
    assert len(payload["items"]) == 1
    assert payload["items"][0]["id"] == "1"


def test_spending_report_json_includes_effective_spending_when_repository_available(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _Repo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return PROFILE_ID

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            assert profile_id == PROFILE_ID
            assert user_id == AUTH_USER_ID
            return {"state": {"last_query": {"month": "2026-01"}}}

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())

    repository = InMemorySharedExpensesRepository()
    repository.seed_shared_expenses(
        [
            SharedExpenseRow(
                from_profile_id=PROFILE_ID,
                to_profile_id=None,
                transaction_id=None,
                amount=Decimal("30"),
                other_party_label="Conjoint",
                created_at=datetime(2026, 1, 10, tzinfo=timezone.utc),
                status="settled",
                split_ratio_other=Decimal("0.5"),
            ),
            SharedExpenseRow(
                from_profile_id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
                to_profile_id=PROFILE_ID,
                transaction_id=None,
                amount=Decimal("10"),
                created_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
                status="settled",
                split_ratio_other=Decimal("0.5"),
            ),
        ]
    )
    monkeypatch.setattr(agent_api, "_try_get_shared_expenses_repository", lambda: repository)

    class _Router:
        def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
            assert profile_id == PROFILE_ID
            if tool_name == "finance_releves_sum":
                return {"total": "-120", "count": 2, "currency": "CHF"}
            if tool_name == "finance_releves_aggregate":
                return {"group_by": "categorie", "currency": "CHF", "groups": {"Courses": {"total": "-120", "count": 2}}}
            if tool_name == "finance_releves_search":
                return {"items": [], "total": 0}
            raise AssertionError(tool_name)

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    response = client.get("/finance/reports/spending?month=2026-01", headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["effective_spending"] == {
        "outgoing": "30",
        "incoming": "10",
        "net_balance": "-20",
        "effective_total": "100",
    }


def test_spending_report_json_neutralizes_effective_spending_when_repository_none(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _Repo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            return PROFILE_ID

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            return {"state": {"last_query": {"month": "2026-01"}}}

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())
    monkeypatch.setattr(agent_api, "_try_get_shared_expenses_repository", lambda: None)

    class _Router:
        def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
            if tool_name == "finance_releves_sum":
                return {"total": "-50", "count": 1, "currency": "CHF"}
            if tool_name == "finance_releves_aggregate":
                return {"group_by": "categorie", "currency": "CHF", "groups": {}}
            if tool_name == "finance_releves_search":
                return {"items": [], "total": 0}
            raise AssertionError(tool_name)

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    response = client.get("/finance/reports/spending?month=2026-01", headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()["effective_spending"] == {
        "outgoing": "0",
        "incoming": "0",
        "net_balance": "0",
        "effective_total": "50",
    }


def test_spending_report_json_neutralizes_when_shared_expenses_table_missing(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _Repo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            return PROFILE_ID

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            return {"state": {"last_query": {"month": "2026-01"}}}

    class _FailingRepository:
        def list_shared_expenses_for_period(self, **_: object) -> list[SharedExpenseRow]:
            raise RuntimeError('relation "shared_expenses" does not exist')

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())
    monkeypatch.setattr(agent_api, "_try_get_shared_expenses_repository", lambda: _FailingRepository())

    class _Router:
        def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
            if tool_name == "finance_releves_sum":
                return {"total": "-80", "count": 1, "currency": "CHF"}
            if tool_name == "finance_releves_aggregate":
                return {"group_by": "categorie", "currency": "CHF", "groups": {}}
            if tool_name == "finance_releves_search":
                return {"items": [], "total": 0}
            raise AssertionError(tool_name)

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    response = client.get("/finance/reports/spending?month=2026-01", headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()["effective_spending"] == {
        "outgoing": "0",
        "incoming": "0",
        "net_balance": "0",
        "effective_total": "80",
    }

def test_spending_report_json_resolves_transaction_category_from_category_id(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _Repo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            return PROFILE_ID

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            return {"state": {"last_query": {"month": "2026-01"}}}

        def get_profile_category_name_by_id(self, *, profile_id: UUID, category_id: UUID) -> str | None:
            assert profile_id == PROFILE_ID
            assert category_id == UUID("11111111-2222-3333-4444-555555555555")
            return "Alimentation"

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())

    class _Router:
        def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
            assert profile_id == PROFILE_ID
            if tool_name == "finance_releves_sum":
                return {"total": "-12.34", "count": 1, "currency": "CHF"}
            if tool_name == "finance_releves_aggregate":
                return {"group_by": "categorie", "currency": "CHF", "groups": {"Autres": {"total": "-12.34", "count": 1}}}
            if tool_name == "finance_releves_search":
                return {
                    "items": [
                        {
                            "date": "2026-01-10",
                            "montant": "-12.34",
                            "categorie": None,
                            "category_id": "11111111-2222-3333-4444-555555555555",
                            "meta": {"category_key": "other"},
                            "payee": "Migros",
                        }
                    ],
                    "total": 1,
                }
            raise AssertionError(tool_name)

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    response = client.get("/finance/reports/spending?month=2026-01", headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["transactions"][0]["category"] == "Alimentation"




def test_spending_report_json_uses_metadata_category_key_when_category_id_resolves_autres(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _Repo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            return PROFILE_ID

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            return {"state": {"last_query": {"month": "2026-01"}}}

        def get_profile_category_name_by_id(self, *, profile_id: UUID, category_id: UUID) -> str | None:
            assert profile_id == PROFILE_ID
            assert category_id == UUID("aaaaaaaa-1111-2222-3333-444444444444")
            return "Autres"

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())

    class _Router:
        def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
            assert profile_id == PROFILE_ID
            if tool_name == "finance_releves_sum":
                return {"total": "-12.34", "count": 1, "currency": "CHF"}
            if tool_name == "finance_releves_aggregate":
                return {"group_by": "categorie", "currency": "CHF", "groups": {"Autres": {"total": "-12.34", "count": 1}}}
            if tool_name == "finance_releves_search":
                return {
                    "items": [
                        {
                            "date": "2026-01-10",
                            "montant": "-12.34",
                            "categorie": None,
                            "category_id": "aaaaaaaa-1111-2222-3333-444444444444",
                            "metadonnees": '{"category_key":"food","tx_kind":"expense"}',
                            "payee": "Migros",
                        }
                    ],
                    "total": 1,
                }
            raise AssertionError(tool_name)

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    response = client.get("/finance/reports/spending?month=2026-01", headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["transactions"][0]["category"] == "Alimentation"
    assert payload["transactions"][0]["flow_type"] == "expense"


def test_spending_report_json_parses_string_metadata_for_category_and_flow_type(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _Repo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            return PROFILE_ID

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            return {"state": {"last_query": {"month": "2026-01"}}}

        def get_profile_category_name_by_id(self, *, profile_id: UUID, category_id: UUID) -> str | None:
            assert profile_id == PROFILE_ID
            assert category_id == UUID("aaaaaaaa-1111-2222-3333-444444444444")
            return None

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())

    class _Router:
        def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
            assert profile_id == PROFILE_ID
            if tool_name == "finance_releves_sum":
                return {"total": "-12.34", "count": 1, "currency": "CHF"}
            if tool_name == "finance_releves_aggregate":
                return {"group_by": "categorie", "currency": "CHF", "groups": {"Autres": {"total": "-12.34", "count": 1}}}
            if tool_name == "finance_releves_search":
                return {
                    "items": [
                        {
                            "date": "2026-01-10",
                            "montant": "-12.34",
                            "categorie": None,
                            "category_id": "aaaaaaaa-1111-2222-3333-444444444444",
                            "metadonnees": '{"category_key":"food","tx_kind":"expense"}',
                            "payee": "Migros",
                        }
                    ],
                    "total": 1,
                }
            raise AssertionError(tool_name)

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    response = client.get("/finance/reports/spending?month=2026-01", headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["transactions"][0]["category"] == "Alimentation"
    assert payload["transactions"][0]["flow_type"] == "expense"
