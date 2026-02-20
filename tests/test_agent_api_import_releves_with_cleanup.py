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


def test_import_releves_auto_apply_cleanup(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )
    monkeypatch.setattr(agent_api._config, "llm_enabled", lambda: True)

    class _Repo:
        def __init__(self) -> None:
            self.rename_calls: list[tuple[UUID, UUID, str]] = []
            self.merge_calls: list[tuple[UUID, UUID, UUID]] = []
            self.category_calls: list[tuple[UUID, str]] = []
            self.inserted_rows: list[dict] = []
            self.list_merchants_calls = 0
            self.get_merchant_by_id_calls: list[UUID] = []

        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            assert auth_user_id == AUTH_USER_ID
            assert email == "user@example.com"
            return PROFILE_ID

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            return {}

        def update_chat_state(self, *, profile_id: UUID, user_id: UUID, chat_state: dict):
            return None

        def list_releves_without_merchant(self, *, profile_id: UUID, limit: int = 500):
            return []

        def list_merchants(self, *, profile_id: UUID, limit: int = 5000):
            self.list_merchants_calls += 1
            return [
                {"id": str(UUID("11111111-1111-1111-1111-111111111111")), "category": ""},
                {"id": str(UUID("22222222-2222-2222-2222-222222222222")), "category": ""},
                {"id": str(UUID("33333333-3333-3333-3333-333333333333")), "category": ""},
            ]

        def get_merchant_by_id(self, *, profile_id: UUID, merchant_id: UUID):
            self.get_merchant_by_id_calls.append(merchant_id)
            return None

        def rename_merchant(self, *, profile_id: UUID, merchant_id: UUID, new_name: str):
            self.rename_calls.append((profile_id, merchant_id, new_name))
            return {}

        def merge_merchants(self, *, profile_id: UUID, source_merchant_id: UUID, target_merchant_id: UUID):
            self.merge_calls.append((profile_id, source_merchant_id, target_merchant_id))
            return {}

        def update_merchant_category(self, *, merchant_id: UUID, category_name: str) -> None:
            self.category_calls.append((merchant_id, category_name))

        def create_merchant_suggestions(self, *, profile_id: UUID, suggestions: list[dict]):
            assert profile_id == PROFILE_ID
            self.inserted_rows.extend(suggestions)
            return len(suggestions)

    repo = _Repo()
    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)

    class _Router:
        def call(self, tool_name: str, _payload: dict, *, profile_id: UUID | None = None):
            assert profile_id == PROFILE_ID
            assert tool_name == "finance_releves_import_files"
            return {"imported_count": 4}

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    def _run_cleanup(**kwargs):
        assert kwargs["merchants"]
        return [
            agent_api.MerchantSuggestion(
                action="rename",
                source_merchant_id=UUID("11111111-1111-1111-1111-111111111111"),
                target_merchant_id=None,
                suggested_name="Coop",
                suggested_category=None,
                confidence=0.95,
                rationale="clear brand",
                sample_aliases=["COOP-123"],
            ),
            agent_api.MerchantSuggestion(
                action="merge",
                source_merchant_id=UUID("22222222-2222-2222-2222-222222222222"),
                target_merchant_id=UUID("11111111-1111-1111-1111-111111111111"),
                suggested_name=None,
                suggested_category=None,
                confidence=0.80,
                rationale="possible duplicate",
                sample_aliases=["COOP CITY"],
            ),
            agent_api.MerchantSuggestion(
                action="categorize",
                source_merchant_id=UUID("33333333-3333-3333-3333-333333333333"),
                target_merchant_id=None,
                suggested_name=None,
                suggested_category="Transport",
                confidence=0.93,
                rationale="sbb",
                sample_aliases=["SBB"],
            ),
        ]

    monkeypatch.setattr(agent_api, "run_merchant_cleanup", _run_cleanup)

    response = client.post(
        "/finance/releves/import",
        headers=_headers(),
        json={"files": [{"filename": "x.csv", "content_base64": "YQ=="}]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["merchant_suggestions_applied_count"] == 2
    assert payload["merchant_suggestions_pending_count"] == 1
    assert repo.rename_calls == [(PROFILE_ID, UUID("11111111-1111-1111-1111-111111111111"), "Coop")]
    assert repo.merge_calls == []
    assert repo.category_calls == [(UUID("33333333-3333-3333-3333-333333333333"), "Transport")]
    assert repo.list_merchants_calls == 1
    assert repo.get_merchant_by_id_calls == []
    assert len(repo.inserted_rows) == 3
    statuses = [row["status"] for row in repo.inserted_rows]
    assert statuses.count("applied") == 2
    assert statuses.count("pending") == 1


def test_maybe_auto_apply_categorize_refetches_missing_snapshot_merchant() -> None:
    class _Repo:
        def __init__(self) -> None:
            self.get_merchant_by_id_calls: list[tuple[UUID, UUID]] = []
            self.category_calls: list[tuple[UUID, str]] = []

        def get_merchant_by_id(self, *, profile_id: UUID, merchant_id: UUID):
            self.get_merchant_by_id_calls.append((profile_id, merchant_id))
            return {"id": str(merchant_id), "category": ""}

        def update_merchant_category(self, *, merchant_id: UUID, category_name: str) -> None:
            self.category_calls.append((merchant_id, category_name))

    repo = _Repo()
    merchant_id = UUID("44444444-4444-4444-4444-444444444444")
    suggestion = agent_api.MerchantSuggestion(
        action="categorize",
        source_merchant_id=merchant_id,
        target_merchant_id=None,
        suggested_name=None,
        suggested_category="Courses",
        confidence=0.95,
        rationale="",
        sample_aliases=[],
    )

    auto_applied, error = agent_api._maybe_auto_apply_suggestion(
        profiles_repository=repo,
        profile_id=PROFILE_ID,
        suggestion=suggestion,
        merchants_by_id={},
    )

    assert error is None
    assert auto_applied is True
    assert repo.get_merchant_by_id_calls == [(PROFILE_ID, merchant_id)]
    assert repo.category_calls == [(merchant_id, "Courses")]


def test_import_releves_cleanup_failure_does_not_increment_failed_count(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )
    monkeypatch.setattr(agent_api._config, "llm_enabled", lambda: True)

    class _Repo:
        def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None):
            return PROFILE_ID

        def get_chat_state(self, *, profile_id: UUID, user_id: UUID):
            return {}

        def update_chat_state(self, *, profile_id: UUID, user_id: UUID, chat_state: dict):
            return None

        def list_releves_without_merchant(self, *, profile_id: UUID, limit: int = 500):
            return []

        def list_merchants(self, *, profile_id: UUID, limit: int = 5000):
            raise RuntimeError("boom")

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())

    class _Router:
        def call(self, tool_name: str, _payload: dict, *, profile_id: UUID | None = None):
            return {"imported_count": 1}

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    response = client.post(
        "/finance/releves/import",
        headers=_headers(),
        json={"files": [{"filename": "x.csv", "content_base64": "YQ=="}]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["merchant_suggestions_failed_count"] == 0
    assert "merchant_cleanup_failed" in payload["warnings"]
