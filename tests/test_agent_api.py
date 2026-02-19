"""Tests for the FastAPI agent endpoints."""

from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

import agent.api as agent_api
from agent.api import app
from agent.loop import AgentLoop
from shared.models import ProfileDataResult, ToolError, ToolErrorCode


client = TestClient(app)
AUTH_USER_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def _mock_authenticated(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )

    class _Repo:
        def __init__(self) -> None:
            self.chat_state: dict[str, object] = {}

        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            assert profile_id == UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
            assert user_id == AUTH_USER_ID
            return self.chat_state

        def update_chat_state(self, *, profile_id: UUID, user_id: UUID, chat_state: dict[str, object]) -> None:
            assert profile_id == UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
            assert user_id == AUTH_USER_ID
            self.chat_state = chat_state

    agent_api.get_profiles_repository.cache_clear()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())

class _DeleteRouter:
    def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
        assert profile_id == UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        if tool_name == "finance_categories_list":
            return type(
                "_CategoriesListResult",
                (),
                {"items": [type("_Category", (), {"name": "Transport"})]},
            )()
        assert tool_name == "finance_categories_delete"
        assert payload["category_name"] == "Transport"
        return None


def test_get_agent_loop_uses_agent_loop_module(monkeypatch) -> None:
    agent_api.get_agent_loop.cache_clear()

    class _DummyBackendClient:
        pass

    class _DummyToolRouter:
        pass

    monkeypatch.setattr(agent_api, "build_backend_tool_service", lambda: SimpleNamespace())
    monkeypatch.setattr(
        agent_api,
        "BackendClient",
        lambda tool_service: _DummyBackendClient(),
    )
    monkeypatch.setattr(
        agent_api,
        "ToolRouter",
        lambda backend_client: _DummyToolRouter(),
    )
    monkeypatch.setattr(agent_api._config, "llm_enabled", lambda: False)

    try:
        loop = agent_api.get_agent_loop()

        assert isinstance(loop, AgentLoop)
        assert loop.__class__.__module__ == "agent.loop"
    finally:
        agent_api.get_agent_loop.cache_clear()


def test_health_endpoint() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_agent_chat_requires_authorization_header() -> None:
    response = client.post("/agent/chat", json={"message": "ping"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing Authorization header"


def test_agent_chat_ping_pong(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    response = client.post("/agent/chat", json={"message": "ping"}, headers=_auth_headers())

    assert response.status_code == 200
    assert response.json() == {"reply": "pong", "tool_result": None, "plan": None}


def test_agent_chat_search_returns_tool_result(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    response = client.post("/agent/chat", json={"message": "search: coffee"}, headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload["reply"], str)
    assert payload["reply"]
    assert isinstance(payload["tool_result"], dict)
    assert isinstance(payload["plan"], dict)
    assert payload["plan"]["tool_name"] == "finance_releves_search"
    assert (
        "items" in payload["tool_result"]
        or {"code", "message"}.issubset(set(payload["tool_result"].keys()))
    )


def test_agent_chat_search_supports_date_range_filters(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    response = client.post(
        "/agent/chat",
        json={"message": "search: coffee from:2025-01-01 to:2025-01-31"},
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload["tool_result"], dict)
    assert "items" in payload["tool_result"]
    assert payload["tool_result"]["items"]
    assert all(("coffee" in (item.get("libelle") or "").lower()) or ("coffee" in (item.get("payee") or "").lower()) for item in payload["tool_result"]["items"])


def test_agent_chat_search_returns_validation_error_for_invalid_limit(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    response = client.post(
        "/agent/chat", json={"message": "search: coffee limit:0"}, headers=_auth_headers()
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["tool_result"]["code"] == "VALIDATION_ERROR"
    assert "details" in payload["tool_result"]


def test_agent_chat_search_returns_parse_validation_error(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    response = client.post(
        "/agent/chat", json={"message": "search: coffee from:2025-01-01"}, headers=_auth_headers()
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["tool_result"]["code"] == "VALIDATION_ERROR"
    assert "details" in payload["tool_result"]


def test_agent_chat_returns_unauthorized_when_auth_user_id_missing(monkeypatch) -> None:
    monkeypatch.setattr(agent_api, "get_user_from_bearer_token", lambda _token: {"email": "x@example.com"})

    response = client.post("/agent/chat", json={"message": "ping"}, headers=_auth_headers())

    assert response.status_code == 401
    assert response.json()["detail"] == "Unauthorized"


def test_agent_chat_profile_lookup_supports_fallback_email(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )

    class _Repo:
        def __init__(self) -> None:
            self.called = False

        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            self.called = True
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            # Simule le fallback interne par email (account_id non trouvé)
            return UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            assert user_id == AUTH_USER_ID
            return {}

        def update_chat_state(self, *, profile_id: UUID, user_id: UUID, chat_state: dict[str, object]) -> None:
            assert user_id == AUTH_USER_ID
            return None

    repo = _Repo()
    agent_api.get_profiles_repository.cache_clear()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)

    response = client.post("/agent/chat", json={"message": "ping"}, headers=_auth_headers())

    assert repo.called is True
    assert response.status_code == 200


def test_agent_chat_returns_not_linked_message_when_profile_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )

    class _Repo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return None

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            assert user_id == AUTH_USER_ID
            return {}

        def update_chat_state(self, *, profile_id: UUID, user_id: UUID, chat_state: dict[str, object]) -> None:
            assert user_id == AUTH_USER_ID
            return None

    agent_api.get_profiles_repository.cache_clear()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())

    response = client.post("/agent/chat", json={"message": "ping"}, headers=_auth_headers())

    assert response.status_code == 401
    assert response.json()["detail"] == "No profile linked to authenticated user (by account_id or email)"


def test_agent_chat_delete_returns_json_reply_when_tool_returns_none(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: AgentLoop(tool_router=_DeleteRouter()))

    response = client.post(
        "/agent/chat",
        json={"message": 'Supprime la catégorie "Transport"'},
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()
    assert isinstance(payload["reply"], str)
    assert payload["reply"]
    assert payload["plan"] is None
    assert payload["tool_result"] is None
    assert "Répondez OUI ou NON" in payload["reply"]



def test_agent_chat_delete_confirmation_workflow(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )

    class _Repo:
        def __init__(self) -> None:
            self.chat_state: dict[str, object] = {}

        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            assert user_id == AUTH_USER_ID
            return self.chat_state

        def update_chat_state(self, *, profile_id: UUID, user_id: UUID, chat_state: dict[str, object]) -> None:
            assert user_id == AUTH_USER_ID
            self.chat_state = chat_state

    class _Router:
        def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
            if tool_name == "finance_categories_list":
                return type(
                    "_CategoriesListResult",
                    (),
                    {"items": [type("_Category", (), {"name": "autres"})]},
                )()
            assert tool_name == "finance_categories_delete"
            assert payload == {"category_name": "autres"}
            return {"ok": True}

    repo = _Repo()
    agent_api.get_profiles_repository.cache_clear()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: AgentLoop(tool_router=_Router()))

    first = client.post(
        "/agent/chat",
        json={"message": "Supprime la catégorie autres"},
        headers=_auth_headers(),
    )
    assert first.status_code == 200
    assert "Répondez OUI ou NON" in first.json()["reply"]
    assert repo.chat_state.get("active_task", {}).get("type") == "needs_confirmation"

    second = client.post(
        "/agent/chat",
        json={"message": "OUI"},
        headers=_auth_headers(),
    )
    assert second.status_code == 200
    assert second.json()["plan"]["tool_name"] == "finance_categories_delete"
    assert repo.chat_state.get("active_task") is None



def test_agent_chat_serializes_pydantic_tool_result(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _Loop:
        def handle_user_message(self, *_args, **_kwargs):
            return SimpleNamespace(
                reply="tool error",
                tool_result=ToolError(code=ToolErrorCode.BACKEND_ERROR, message="boom"),
                plan={"tool_name": "finance_releves_search"},
                should_update_active_task=False,
                active_task=None,
            )

    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _Loop())

    response = client.post("/agent/chat", json={"message": "ping"}, headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload["tool_result"], dict)
    assert payload["tool_result"]["code"] == "BACKEND_ERROR"
    assert payload["tool_result"]["message"] == "boom"

def test_agent_chat_returns_200_when_chat_state_update_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )

    class _Repo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            assert user_id == AUTH_USER_ID
            return {}

        def update_chat_state(self, *, profile_id: UUID, user_id: UUID, chat_state: dict[str, object]) -> None:
            assert user_id == AUTH_USER_ID
            raise RuntimeError("db write failed")

    class _Loop:
        def handle_user_message(self, *_args, **_kwargs):
            return SimpleNamespace(
                reply="ok",
                tool_result={"ok": True},
                plan={"tool_name": "finance_releves_search"},
                should_update_active_task=True,
                active_task={"type": "any"},
            )

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _Loop())

    response = client.post("/agent/chat", json={"message": "ping"}, headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"] == "ok"
    assert payload["plan"]["warnings"] == ["chat_state_update_failed"]


def test_agent_chat_returns_fallback_when_agent_loop_fails(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _Loop:
        def handle_user_message(self, *_args, **_kwargs):
            raise RuntimeError("agent loop down")

    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _Loop())

    response = client.post("/agent/chat", json={"message": "ping"}, headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"]
    assert payload["tool_result"] == {"error": "internal_server_error"}


class _ProfileRouter:
    def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
        assert profile_id == UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        if tool_name == "finance_profile_get" and payload == {"fields": ["city"]}:
            return ProfileDataResult(
                profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                data={"city": "Lausanne"},
            )
        raise AssertionError(f"Unexpected tool call: {tool_name} {payload}")


def test_agent_chat_profile_city_question_returns_200_with_city(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)
    monkeypatch.setattr(agent_api, "get_agent_loop", lambda: AgentLoop(tool_router=_ProfileRouter()))

    response = client.post(
        "/agent/chat",
        json={"message": "Quelle est ma ville ?"},
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"] == "Votre ville est: Lausanne."


def test_agent_chat_profile_unknown_field_returns_validation_message(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    response = client.post(
        "/agent/chat",
        json={"message": "Quelle est ma couleur préférée ?"},
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert "Je n’ai pas compris quelle info du profil vous voulez" in payload["reply"]
    assert payload["tool_result"]["code"] == "VALIDATION_ERROR"


def test_agent_reset_session_clears_active_task(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )

    class _Repo:
        def __init__(self) -> None:
            self.chat_state: dict[str, object] = {
                "active_task": {"type": "awaiting_search_merchant"},
                "state": {
                    "pending_clarification": {"field": "merchant"},
                    "last_query": {"last_tool_name": "finance_releves_search"},
                },
            }
            self.update_calls: list[dict[str, object]] = []

        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            assert profile_id == UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
            assert user_id == AUTH_USER_ID
            return self.chat_state

        def update_chat_state(self, *, profile_id: UUID, user_id: UUID, chat_state: dict[str, object]) -> None:
            assert profile_id == UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
            assert user_id == AUTH_USER_ID
            self.update_calls.append({"chat_state": chat_state})
            self.chat_state = chat_state

    repo = _Repo()
    agent_api.get_profiles_repository.cache_clear()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)

    response = client.post("/agent/reset-session", headers=_auth_headers())

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert repo.update_calls[-1]["chat_state"]["active_task"] is None
    assert repo.update_calls[-1]["chat_state"]["state"] == {
        "last_query": {"last_tool_name": "finance_releves_search"}
    }
    assert "pending_clarification" not in repo.update_calls[-1]["chat_state"]["state"]


def test_agent_reset_session_removes_empty_state_after_clearing_pending_clarification(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )

    class _Repo:
        def __init__(self) -> None:
            self.chat_state: dict[str, object] = {
                "active_task": {"type": "awaiting_search_merchant"},
                "state": {"pending_clarification": {"field": "merchant"}},
            }
            self.update_calls: list[dict[str, object]] = []

        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            assert profile_id == UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
            assert user_id == AUTH_USER_ID
            return self.chat_state

        def update_chat_state(self, *, profile_id: UUID, user_id: UUID, chat_state: dict[str, object]) -> None:
            assert profile_id == UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
            assert user_id == AUTH_USER_ID
            self.update_calls.append({"chat_state": chat_state})
            self.chat_state = chat_state

    repo = _Repo()
    agent_api.get_profiles_repository.cache_clear()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)

    response = client.post("/agent/reset-session", headers=_auth_headers())

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert repo.update_calls[-1]["chat_state"]["active_task"] is None
    assert "state" not in repo.update_calls[-1]["chat_state"]


def test_debug_hard_reset_requires_confirm_true(monkeypatch) -> None:
    monkeypatch.setenv("DEBUG_ENDPOINTS_ENABLED", "true")
    _mock_authenticated(monkeypatch)

    response = client.post('/debug/hard-reset', json={}, headers=_auth_headers())

    assert response.status_code == 400


def test_debug_hard_reset_returns_404_when_debug_disabled(monkeypatch) -> None:
    monkeypatch.delenv("DEBUG_ENDPOINTS_ENABLED", raising=False)
    _mock_authenticated(monkeypatch)

    response = client.post('/debug/hard-reset', json={'confirm': True}, headers=_auth_headers())

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


def test_debug_hard_reset_works_when_debug_enabled(monkeypatch) -> None:
    monkeypatch.setenv("DEBUG_ENDPOINTS_ENABLED", "true")
    monkeypatch.setattr(
        agent_api,
        'get_user_from_bearer_token',
        lambda _token: {'id': str(AUTH_USER_ID), 'email': 'user@example.com'},
    )

    class _Repo:
        def __init__(self) -> None:
            self.called_with: dict[str, UUID] | None = None

        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == 'user@example.com'
            return UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')

        def hard_reset_profile(self, *, profile_id: UUID, user_id: UUID) -> None:
            self.called_with = {'profile_id': profile_id, 'user_id': user_id}

    repo = _Repo()
    agent_api.get_profiles_repository.cache_clear()
    monkeypatch.setattr(agent_api, 'get_profiles_repository', lambda: repo)

    response = client.post('/debug/hard-reset', json={'confirm': True}, headers=_auth_headers())

    assert response.status_code == 200
    assert response.json() == {'ok': True}
    assert repo.called_with == {
        'profile_id': UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'),
        'user_id': AUTH_USER_ID,
    }
