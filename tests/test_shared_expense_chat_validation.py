"""Tests for shared-expense chat confirmation deterministic flow."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi.testclient import TestClient

import agent.api as agent_api
from agent.api import app, parse_shared_expense_confirmation
from backend.repositories.shared_expenses_repository import SharedExpenseSuggestionRow


client = TestClient(app)
AUTH_USER_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SUGGESTION_ID_1 = UUID("11111111-1111-1111-1111-111111111111")
SUGGESTION_ID_2 = UUID("22222222-2222-2222-2222-222222222222")
TX_ID_1 = UUID("33333333-3333-3333-3333-333333333333")
TX_ID_2 = UUID("44444444-4444-4444-4444-444444444444")
OTHER_PROFILE_ID = UUID("55555555-5555-5555-5555-555555555555")


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


class _Repo:
    def __init__(self, chat_state: dict[str, object] | None = None) -> None:
        self.chat_state = chat_state or {}
        self.updated_chat_states: list[dict[str, object]] = []

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
        self.updated_chat_states.append(dict(chat_state))


class _SuggestionsRepository:
    def __init__(self) -> None:
        self.applied_calls: list[dict[str, object]] = []

    def list_shared_expense_suggestions(self, *, profile_id: UUID, status: str, limit: int):
        assert profile_id == PROFILE_ID
        assert status == "pending"
        assert limit == 50
        return [
            SharedExpenseSuggestionRow(
                id=SUGGESTION_ID_1,
                profile_id=PROFILE_ID,
                transaction_id=TX_ID_1,
                suggested_to_profile_id=OTHER_PROFILE_ID,
                suggested_split_ratio_other=Decimal("0.5"),
                status="pending",
                confidence=0.9,
                rationale=None,
                link_id=None,
                link_pair_id=None,
            ),
            SharedExpenseSuggestionRow(
                id=SUGGESTION_ID_2,
                profile_id=PROFILE_ID,
                transaction_id=TX_ID_2,
                suggested_to_profile_id=None,
                suggested_split_ratio_other=Decimal("0.5"),
                status="pending",
                confidence=0.9,
                rationale=None,
                link_id=None,
                link_pair_id=None,
                other_party_label="Conjoint",
            ),
        ]

    def create_shared_expense_from_suggestion(self, *, profile_id: UUID, suggestion_id: UUID, amount: Decimal):
        self.applied_calls.append({"profile_id": profile_id, "suggestion_id": suggestion_id, "amount": amount})
        return UUID(int=6)


def test_parse_shared_expense_confirmation_cases() -> None:
    snapshot = [
        {"index": 1, "suggestion_id": str(SUGGESTION_ID_1)},
        {"index": 2, "suggestion_id": str(SUGGESTION_ID_2)},
        {"index": 3, "suggestion_id": str(UUID(int=7))},
    ]

    assert parse_shared_expense_confirmation("oui tout", snapshot) == {
        "kind": "apply_all",
        "indices": [1, 2, 3],
    }
    assert parse_shared_expense_confirmation("oui 1 et 3", snapshot) == {
        "kind": "apply_subset",
        "indices": [1, 3],
    }
    assert parse_shared_expense_confirmation("non 2", snapshot) == {
        "kind": "dismiss_subset",
        "indices": [2],
    }

    split = parse_shared_expense_confirmation("split 2 60/40", snapshot)
    assert split["kind"] == "split_apply"
    assert split["indices"] == [2]
    assert split["ratio_other"] == Decimal("0.4000")


def test_agent_chat_shared_expense_intent_lists_pending_and_persists_active_task(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )
    repo = _Repo()
    suggestions_repo = _SuggestionsRepository()

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "_get_shared_expenses_repository_or_501", lambda: suggestions_repo)
    monkeypatch.setattr(agent_api._config, "supabase_url", lambda: "http://localhost")
    monkeypatch.setattr(agent_api._config, "supabase_service_role_key", lambda: "service")
    monkeypatch.setattr(agent_api._config, "supabase_anon_key", lambda: "anon")

    class _FakeSupabaseClient:
        def __init__(self, settings):
            self.settings = settings

        def get_rows(self, *, table, query, with_count, use_anon_key):
            assert table == "releves_bancaires"
            assert with_count is False
            assert use_anon_key is False
            return [
                {
                    "id": str(TX_ID_1),
                    "date": "2026-02-05",
                    "montant": "80",
                    "devise": "CHF",
                    "payee": "Migros",
                    "libelle": "Migros",
                },
                {
                    "id": str(TX_ID_2),
                    "date": "2026-02-10",
                    "montant": "2500",
                    "devise": "CHF",
                    "payee": "Régie",
                    "libelle": "Regie",
                },
            ], None

    monkeypatch.setattr(agent_api, "SupabaseClient", _FakeSupabaseClient)

    response = client.post("/agent/chat", json={"message": "Peux-tu valider les dépenses partagées ?"}, headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert "1) 2026-02-05" in payload["reply"]
    assert "Réponds:" in payload["reply"]
    assert "Conjoint" in payload["reply"]
    active_task = repo.chat_state.get("active_task")
    assert isinstance(active_task, dict)
    assert active_task["type"] == "shared_expense_confirm"
    assert len(active_task["suggestions"]) == 2


def test_agent_chat_shared_expense_active_task_applies_selected_index(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )
    repo = _Repo(
        chat_state={
            "active_task": {
                "type": "shared_expense_confirm",
                "created_at": "2026-01-01T10:00:00",
                "suggestions": [
                    {
                        "index": 1,
                        "suggestion_id": str(SUGGESTION_ID_1),
                        "transaction_id": str(TX_ID_1),
                        "date": "2026-02-05",
                        "merchant": "Migros",
                        "amount": "80.00",
                        "currency": "CHF",
                        "suggested_split_ratio_other": "0.5",
                        "suggested_to_profile_id": str(OTHER_PROFILE_ID),
                    }
                ],
            }
        }
    )
    suggestions_repo = _SuggestionsRepository()

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "_get_shared_expenses_repository_or_501", lambda: suggestions_repo)

    response = client.post("/agent/chat", json={"message": "oui 1"}, headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()["reply"].startswith("Terminé")
    assert suggestions_repo.applied_calls == [
        {
            "profile_id": PROFILE_ID,
            "suggestion_id": SUGGESTION_ID_1,
            "amount": Decimal("40.00"),
        }
    ]
    assert repo.chat_state.get("active_task") is None


def test_agent_chat_account_link_setup_external_flow(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )
    repo = _Repo()

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)

    response_start = client.post("/agent/chat", json={"message": "je veux lier compte pour mon foyer"}, headers=_auth_headers())
    assert response_start.status_code == 200
    assert "interne" in response_start.json()["reply"].lower()

    response_type = client.post("/agent/chat", json={"message": "externe"}, headers=_auth_headers())
    assert response_type.status_code == 200
    assert "libellé" in response_type.json()["reply"].lower()

    response_label = client.post("/agent/chat", json={"message": "Conjoint"}, headers=_auth_headers())
    assert response_label.status_code == 200
    assert "ratio" in response_label.json()["reply"].lower()

    response_split = client.post("/agent/chat", json={"message": "60/40"}, headers=_auth_headers())
    assert response_split.status_code == 200
    assert "enregistrée" in response_split.json()["reply"]
    assert repo.chat_state.get("active_task") is None

    state = repo.chat_state.get("state")
    assert isinstance(state, dict)
    household_link = state.get("global_state", {}).get("household_link")
    assert household_link == {
        "link_type": "external",
        "other_profile_id": None,
        "other_party_label": "Conjoint",
        "default_split_ratio_other": "0.4000",
        "enabled": True,
    }
