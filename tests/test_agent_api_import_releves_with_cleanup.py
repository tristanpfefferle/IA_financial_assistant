from __future__ import annotations

from uuid import UUID
from typing import Any

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
        return (
            [
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
            ],
            "run123",
            {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            {"raw_count": 3, "parsed_count": 3, "rejected_count": 0, "rejected_reasons": {}},
        )

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
    assert all(row["llm_run_id"] == "run123" for row in repo.inserted_rows)
    statuses = [row["status"] for row in repo.inserted_rows]
    assert statuses.count("applied") == 2
    assert statuses.count("pending") == 1
    assert payload["merchant_cleanup_llm_run_id"] == "run123"
    assert payload["merchant_cleanup_usage"]["total_tokens"] == 150
    assert payload["merchant_cleanup_stats"]["parsed_count"] == 3


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


def test_import_releves_cleanup_no_suggestions_warning(monkeypatch) -> None:
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
            return [{"id": str(UUID("11111111-1111-1111-1111-111111111111")), "category": ""}]

        def create_merchant_suggestions(self, *, profile_id: UUID, suggestions: list[dict]):
            return len(suggestions)

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())

    class _Router:
        def call(self, tool_name: str, _payload: dict, *, profile_id: UUID | None = None):
            return {"imported_count": 1}

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())
    monkeypatch.setattr(
        agent_api,
        "run_merchant_cleanup",
        lambda **_kwargs: (
            [],
            "run123",
            {},
            {"raw_count": 3, "parsed_count": 0, "rejected_count": 3, "rejected_reasons": {"missing_ids": 3}},
        ),
    )

    response = client.post(
        "/finance/releves/import",
        headers=_headers(),
        json={"files": [{"filename": "x.csv", "content_base64": "YQ=="}]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert "merchant_cleanup_no_suggestions" in payload["warnings"]
    assert payload["merchant_cleanup_llm_run_id"] == "run123"
    assert payload["merchant_cleanup_stats"]["parsed_count"] == 0


def test_bootstrap_merchants_from_imported_releves_known_alias_sets_entity_and_category() -> None:
    entity_id = UUID("11111111-1111-1111-1111-111111111111")
    releve_id = UUID("22222222-2222-2222-2222-222222222222")
    category_id = UUID("33333333-3333-3333-3333-333333333333")

    class _Repo:
        def __init__(self) -> None:
            self.attach_calls: list[tuple[UUID, UUID, UUID | None]] = []
            self.override_upserts: list[tuple[UUID, UUID, UUID | None, str]] = []
            self.alias_upserts: list[tuple[UUID, str, str, str]] = []

        def list_releves_without_merchant(self, *, profile_id: UUID, limit: int = 500):
            return [{"id": str(releve_id), "payee": "COOP CITY", "libelle": None}]

        def list_profile_categories(self, *, profile_id: UUID):
            return [{"id": str(category_id), "name_norm": "courses"}]

        def find_merchant_entity_by_alias_norm(self, *, alias_norm: str):
            assert alias_norm == "coop city"
            return {
                "id": str(entity_id),
                "suggested_category_norm": "courses",
                "suggested_category_label": "Courses",
            }

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
            self.alias_upserts.append((merchant_entity_id, alias, alias_norm, source))

        def upsert_profile_merchant_override(
            self,
            *,
            profile_id: UUID,
            merchant_entity_id: UUID,
            category_id: UUID | None,
            status: str = "auto",
        ) -> None:
            self.override_upserts.append((profile_id, merchant_entity_id, category_id, status))

        def create_map_alias_suggestions(self, *, profile_id: UUID, rows: list[dict]):
            return 0

    repo = _Repo()

    summary = agent_api._bootstrap_merchants_from_imported_releves(
        profiles_repository=repo,
        profile_id=PROFILE_ID,
        limit=50,
    )

    assert summary == {"processed_count": 1, "linked_count": 1, "skipped_count": 0}
    assert repo.attach_calls == [(releve_id, entity_id, category_id)]
    assert repo.alias_upserts == [(entity_id, "COOP CITY", "coop city", "import")]
    assert repo.override_upserts == [(PROFILE_ID, entity_id, category_id, "auto")]


def test_bootstrap_merchants_from_imported_releves_unknown_alias_creates_deduped_map_alias_suggestion() -> None:
    releve_id_1 = UUID("aaaaaaaa-1111-1111-1111-111111111111")
    releve_id_2 = UUID("aaaaaaaa-2222-2222-2222-222222222222")

    class _Repo:
        def __init__(self) -> None:
            self.suggestion_rows: list[dict] = []
            self.attach_calls = 0

        def list_releves_without_merchant(self, *, profile_id: UUID, limit: int = 500):
            return [
                {"id": str(releve_id_1), "payee": "Unknown Shop", "libelle": None},
                {"id": str(releve_id_2), "payee": "Unknown Shop", "libelle": None},
            ]

        def list_profile_categories(self, *, profile_id: UUID):
            return []

        def find_merchant_entity_by_alias_norm(self, *, alias_norm: str):
            return None

        def create_map_alias_suggestions(self, *, profile_id: UUID, rows: list[dict]):
            self.suggestion_rows = rows
            return 1

        def attach_merchant_entity_to_releve(self, **kwargs) -> None:
            self.attach_calls += 1

    repo = _Repo()

    summary = agent_api._bootstrap_merchants_from_imported_releves(
        profiles_repository=repo,
        profile_id=PROFILE_ID,
        limit=50,
    )

    assert summary == {"processed_count": 2, "linked_count": 0, "skipped_count": 2}
    assert repo.attach_calls == 0
    assert len(repo.suggestion_rows) == 2
    assert {row["observed_alias_norm"] for row in repo.suggestion_rows} == {"unknown shop"}


def test_bootstrap_merchants_from_imported_releves_does_not_fallback_to_suggested_category_label() -> None:
    entity_id = UUID("44444444-4444-4444-4444-444444444444")
    releve_id = UUID("55555555-5555-5555-5555-555555555555")

    class _Repo:
        def __init__(self) -> None:
            self.attach_calls: list[tuple[UUID, UUID, UUID | None]] = []

        def list_releves_without_merchant(self, *, profile_id: UUID, limit: int = 500):
            return [{"id": str(releve_id), "payee": "COOP CITY", "libelle": None}]

        def list_profile_categories(self, *, profile_id: UUID):
            return [{"id": str(UUID("66666666-6666-6666-6666-666666666666")), "name_norm": "courses"}]

        def find_merchant_entity_by_alias_norm(self, *, alias_norm: str):
            return {
                "id": str(entity_id),
                "suggested_category_norm": "",
                "suggested_category_label": "Courses",
            }

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

        def upsert_merchant_alias(self, **kwargs) -> None:
            return None

        def upsert_profile_merchant_override(self, **kwargs) -> None:
            return None

        def create_map_alias_suggestions(self, *, profile_id: UUID, rows: list[dict]):
            return 0

    repo = _Repo()

    summary = agent_api._bootstrap_merchants_from_imported_releves(
        profiles_repository=repo,
        profile_id=PROFILE_ID,
        limit=50,
    )

    assert summary == {"processed_count": 1, "linked_count": 1, "skipped_count": 0}
    assert repo.attach_calls == [(releve_id, entity_id, None)]


def test_bootstrap_merchants_from_imported_releves_does_not_upsert_override_without_category() -> None:
    entity_id = UUID("77777777-7777-7777-7777-777777777777")
    releve_id = UUID("88888888-8888-8888-8888-888888888888")

    class _Repo:
        def __init__(self) -> None:
            self.override_upserts: list[dict[str, Any]] = []

        def list_releves_without_merchant(self, *, profile_id: UUID, limit: int = 500):
            return [{"id": str(releve_id), "payee": "Unknown", "libelle": None}]

        def list_profile_categories(self, *, profile_id: UUID):
            return []

        def find_merchant_entity_by_alias_norm(self, *, alias_norm: str):
            return {
                "id": str(entity_id),
                "suggested_category_norm": "",
                "suggested_category_label": "",
            }

        def get_profile_merchant_override(self, *, profile_id: UUID, merchant_entity_id: UUID):
            return None

        def attach_merchant_entity_to_releve(self, **kwargs) -> None:
            return None

        def upsert_merchant_alias(self, **kwargs) -> None:
            return None

        def upsert_profile_merchant_override(self, **kwargs) -> None:
            self.override_upserts.append(kwargs)

        def create_map_alias_suggestions(self, *, profile_id: UUID, rows: list[dict]):
            return 0

    repo = _Repo()

    summary = agent_api._bootstrap_merchants_from_imported_releves(
        profiles_repository=repo,
        profile_id=PROFILE_ID,
        limit=50,
    )

    assert summary == {"processed_count": 1, "linked_count": 1, "skipped_count": 0}
    assert repo.override_upserts == []
