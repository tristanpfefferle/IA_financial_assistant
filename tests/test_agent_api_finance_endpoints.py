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
            assert tool_name == "finance_releves_import_files"
            assert profile_id == PROFILE_ID
            assert payload["import_mode"] == "analyze"
            assert payload["modified_action"] == "replace"
            assert payload["bank_account_id"] == str(UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"))
            assert payload["files"][0]["filename"] == "ubs.csv"
            return {"imported_count": 0, "requires_confirmation": True}

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
    assert response.json()["requires_confirmation"] is True


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
