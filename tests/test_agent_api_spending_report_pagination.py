from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from fastapi.testclient import TestClient

import agent.api as agent_api
from agent.api import app


client = TestClient(app)
AUTH_USER_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer token"}


def test_spending_report_fetches_all_transactions_across_pages(monkeypatch) -> None:
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
            return {"state": {"last_query": {"month": "2025-12"}}}

        def update_chat_state(self, *, profile_id: UUID, user_id: UUID, chat_state: dict):
            return None

        def get_profile_category_name_by_id(self, *, profile_id: UUID, category_id: UUID):
            return None

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: _Repo())

    all_transactions = []
    current_day = date(2025, 1, 1)
    for index in range(620):
        all_transactions.append(
            {
                "date": current_day.isoformat(),
                "montant": "-10.00",
                "devise": "CHF",
                "payee": f"Shop {index}",
                "categorie": "Alimentation",
            }
        )
        current_day += timedelta(days=1)

    class _Router:
        def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
            assert profile_id == PROFILE_ID
            if tool_name == "finance_releves_sum":
                return {"total": "-6200.00", "count": 620, "currency": "CHF"}
            if tool_name == "finance_releves_aggregate":
                return {"group_by": "categorie", "currency": "CHF", "groups": {"Alimentation": {"total": "-6200.00", "count": 620}}}
            if tool_name == "finance_releves_search":
                limit = int(payload.get("limit") or 0)
                offset = int(payload.get("offset") or 0)
                return {"items": all_transactions[offset : offset + limit], "total": 620}
            raise AssertionError(tool_name)

    monkeypatch.setattr(agent_api, "get_tool_router", lambda: _Router())

    response = client.get("/finance/reports/spending?start_date=2025-01-01&end_date=2025-12-31", headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 620
    assert len(payload["transactions"]) == 620
    assert payload["transactions"][0]["date"].startswith("2025-01")
    assert payload["transactions_truncated"] is False
