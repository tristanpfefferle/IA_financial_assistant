"""Tests for finance import endpoints exposed by agent.api."""

from uuid import UUID

from fastapi.testclient import TestClient

import agent.api as agent_api
from agent.api import app
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
            self.upsert_calls: list[tuple[str, str]] = []
            self.attach_calls: list[tuple[UUID, UUID]] = []

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

        def list_releves_without_merchant(self, *, profile_id: UUID, limit: int = 500):
            assert profile_id == PROFILE_ID
            assert limit == 500
            return [
                {"id": str(UUID("11111111-1111-1111-1111-111111111111")), "payee": "Migros", "libelle": "", "created_at": None, "date": "2025-01-01"},
                {"id": str(UUID("22222222-2222-2222-2222-222222222222")), "payee": "", "libelle": "SBB", "created_at": None, "date": "2025-01-02"},
            ]

        def upsert_merchant_by_name_norm(self, *, profile_id: UUID, name: str, name_norm: str, scope: str = "personal"):
            assert profile_id == PROFILE_ID
            self.upsert_calls.append((name, name_norm))
            return UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")

        def attach_merchant_to_releve(self, *, releve_id: UUID, merchant_id: UUID) -> None:
            self.attach_calls.append((releve_id, merchant_id))

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
    assert len(repo.upsert_calls) == 2
    assert repo.upsert_calls == [("Migros", "migros"), ("SBB", "sbb")]
    assert len(repo.attach_calls) == 2
