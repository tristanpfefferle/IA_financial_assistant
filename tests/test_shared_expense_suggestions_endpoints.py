"""Tests for shared expense suggestions endpoints exposed by agent.api."""

from decimal import Decimal
from uuid import UUID

from fastapi.testclient import TestClient

import agent.api as agent_api
from agent.api import app
from backend.repositories.shared_expenses_repository import SharedExpenseSuggestionRow


client = TestClient(app)
PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SUGGESTION_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
TRANSACTION_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
OTHER_PROFILE_ID = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def _mock_authenticated(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "_resolve_authenticated_profile",
        lambda _request, _authorization=None: (UUID(int=1), PROFILE_ID),
    )


def test_list_shared_expense_suggestions_returns_501_without_supabase(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)
    monkeypatch.setattr(agent_api._config, "supabase_url", lambda: "")
    monkeypatch.setattr(agent_api._config, "supabase_service_role_key", lambda: "")

    response = client.get("/finance/shared-expenses/suggestions", headers=_auth_headers())

    assert response.status_code == 501
    assert response.json()["detail"] == "shared expenses disabled"


def test_list_shared_expense_suggestions_returns_items(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)
    monkeypatch.setattr(agent_api._config, "supabase_url", lambda: "http://localhost")
    monkeypatch.setattr(agent_api._config, "supabase_service_role_key", lambda: "service-key")
    monkeypatch.setattr(agent_api._config, "supabase_anon_key", lambda: "anon")

    class _FakeRepository:
        def list_shared_expense_suggestions(self, *, profile_id: UUID, status: str = "pending", limit: int = 50):
            assert profile_id == PROFILE_ID
            assert status == "pending"
            assert limit == 50
            return [
                SharedExpenseSuggestionRow(
                    id=SUGGESTION_ID,
                    profile_id=PROFILE_ID,
                    transaction_id=TRANSACTION_ID,
                    suggested_to_profile_id=OTHER_PROFILE_ID,
                    suggested_split_ratio_other=Decimal("0.5"),
                    status="pending",
                    confidence=0.8,
                    rationale="match by rule",
                    link_id=None,
                    link_pair_id=None,
                ),
                SharedExpenseSuggestionRow(
                    id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"),
                    profile_id=PROFILE_ID,
                    transaction_id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
                    suggested_to_profile_id=OTHER_PROFILE_ID,
                    suggested_split_ratio_other=Decimal("0.3"),
                    status="pending",
                    confidence=0.7,
                    rationale=None,
                    link_id=None,
                    link_pair_id=None,
                ),
            ]

    fake_repo = _FakeRepository()
    monkeypatch.setattr(agent_api, "_get_shared_expenses_repository_or_501", lambda: fake_repo)

    response = client.get("/finance/shared-expenses/suggestions", headers=_auth_headers())

    assert response.status_code == 200
    assert len(response.json()["items"]) == 2


def test_dismiss_shared_expense_suggestion_marks_status(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)
    monkeypatch.setattr(agent_api._config, "supabase_url", lambda: "http://localhost")
    monkeypatch.setattr(agent_api._config, "supabase_service_role_key", lambda: "service-key")
    monkeypatch.setattr(agent_api._config, "supabase_anon_key", lambda: "anon")

    called: dict[str, object] = {}

    class _FakeRepository:
        def mark_suggestion_status(self, *, profile_id: UUID, suggestion_id: UUID, status: str, error: str | None = None):
            called["profile_id"] = profile_id
            called["suggestion_id"] = suggestion_id
            called["status"] = status
            called["error"] = error

    monkeypatch.setattr(agent_api, "_get_shared_expenses_repository_or_501", lambda: _FakeRepository())

    response = client.post(
        f"/finance/shared-expenses/suggestions/{SUGGESTION_ID}/dismiss",
        headers=_auth_headers(),
        json={"reason": "invalid"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert called == {
        "profile_id": PROFILE_ID,
        "suggestion_id": SUGGESTION_ID,
        "status": "dismissed",
        "error": "invalid",
    }


def test_apply_shared_expense_suggestion_with_amount(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)
    monkeypatch.setattr(agent_api._config, "supabase_url", lambda: "http://localhost")
    monkeypatch.setattr(agent_api._config, "supabase_service_role_key", lambda: "service-key")
    monkeypatch.setattr(agent_api._config, "supabase_anon_key", lambda: "anon")

    created_shared_expense_id = UUID("11111111-2222-3333-4444-555555555555")
    called: dict[str, object] = {}

    class _FakeRepository:
        def create_shared_expense_from_suggestion(self, *, profile_id: UUID, suggestion_id: UUID, amount: Decimal):
            called["profile_id"] = profile_id
            called["suggestion_id"] = suggestion_id
            called["amount"] = amount
            return created_shared_expense_id

    monkeypatch.setattr(agent_api, "_get_shared_expenses_repository_or_501", lambda: _FakeRepository())

    response = client.post(
        f"/finance/shared-expenses/suggestions/{SUGGESTION_ID}/apply",
        headers=_auth_headers(),
        json={"amount": "42.50"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "shared_expense_id": str(created_shared_expense_id)}
    assert called == {
        "profile_id": PROFILE_ID,
        "suggestion_id": SUGGESTION_ID,
        "amount": Decimal("42.50"),
    }
