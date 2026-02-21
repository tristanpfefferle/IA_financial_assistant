from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient

import agent.api as agent_api
from agent.api import app


client = TestClient(app)
AUTH_USER_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer token"}


def test_agent_chat_resumes_pending_import_after_account_clarification(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )

    class _Repo:
        def __init__(self) -> None:
            self.updated_chat_state: dict | None = None

        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return PROFILE_ID

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            assert profile_id == PROFILE_ID
            assert user_id == AUTH_USER_ID
            return {
                "state": {
                    "import_context": {
                        "pending_files": [{"filename": "statement.csv", "content_base64": "YQ=="}],
                        "clarification_accounts": [
                            {"id": str(UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")), "name": "UBS"},
                            {"id": str(UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")), "name": "Revolut"},
                        ],
                    }
                }
            }

        def list_bank_accounts(self, *, profile_id: UUID):
            assert profile_id == PROFILE_ID
            return [
                {"id": str(UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")), "name": "UBS"},
                {"id": str(UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")), "name": "Revolut"},
            ]

        def update_chat_state(self, *, profile_id: UUID, user_id: UUID, chat_state: dict):
            assert profile_id == PROFILE_ID
            assert user_id == AUTH_USER_ID
            self.updated_chat_state = chat_state

    repo = _Repo()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)

    import_calls: list[dict] = []

    class _Router:
        def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
            assert profile_id == PROFILE_ID
            if tool_name == "finance_releves_import_files":
                import_calls.append(payload)
                return {"imported_count": 3}
            return {"ok": True}

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    response = client.post(
        "/agent/chat",
        headers=_headers(),
        json={"message": "UBS", "request_greeting": False},
    )

    assert response.status_code == 200
    assert import_calls and import_calls[0]["bank_account_id"] == str(UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"))
    assert "3 transactions" in response.json()["reply"]
    assert repo.updated_chat_state is not None
    assert "pending_files" not in repo.updated_chat_state["state"].get("import_context", {})
