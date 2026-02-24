"""Tests for shared-expense chat confirmation deterministic flow."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi.testclient import TestClient

import agent.api as agent_api
from agent.api import app, parse_shared_expense_confirmation
from backend.repositories.shared_expenses_repository import InMemorySharedExpensesRepository, SharedExpenseSuggestionRow


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
        self.upsert_household_link_calls: list[dict[str, object]] = []

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

    def upsert_household_link(
        self,
        *,
        profile_id: UUID,
        link_type: str,
        other_profile_id: UUID | None,
        other_party_label: str | None,
        other_party_email: str | None,
        default_split_ratio_other: str,
    ) -> dict[str, object]:
        payload = {
            "profile_id": profile_id,
            "link_type": link_type,
            "other_profile_id": other_profile_id,
            "other_party_label": other_party_label,
            "other_party_email": other_party_email,
            "default_split_ratio_other": default_split_ratio_other,
        }
        self.upsert_household_link_calls.append(payload)
        return {
            "link_type": link_type,
            "other_profile_id": str(other_profile_id) if other_profile_id else None,
            "other_party_label": other_party_label,
            "other_party_email": other_party_email,
            "default_split_ratio_other": default_split_ratio_other,
        }


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


class _SeedSuggestionsRepository(InMemorySharedExpensesRepository):
    def __init__(self) -> None:
        super().__init__()
        self.bulk_calls: list[list[dict[str, object]]] = []

    def create_shared_expense_suggestions_bulk(self, *, profile_id: UUID, suggestions: list[dict[str, object]]) -> int:
        self.bulk_calls.append(suggestions)
        return super().create_shared_expense_suggestions_bulk(profile_id=profile_id, suggestions=suggestions)


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


def test_agent_chat_account_link_setup_external_flow_triggers_initial_validation_when_candidates_exist(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )
    repo = _Repo()
    suggestions_repo = _SeedSuggestionsRepository()

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "_get_shared_expenses_repository_or_501", lambda: suggestions_repo)
    monkeypatch.setattr(agent_api._config, "supabase_url", lambda: "http://localhost")
    monkeypatch.setattr(agent_api._config, "supabase_service_role_key", lambda: "service-role")
    monkeypatch.setattr(agent_api._config, "supabase_anon_key", lambda: "anon")

    tx_seed_1 = UUID("66666666-6666-6666-6666-666666666666")
    tx_seed_2 = UUID("77777777-7777-7777-7777-777777777777")

    class _FakeSupabaseClient:
        def __init__(self, settings):
            self.settings = settings

        def get_rows(self, *, table, query, with_count, use_anon_key):
            assert table == "releves_bancaires"
            assert with_count is False
            assert use_anon_key is False
            if "category" in str(query.get("select") or ""):
                return [
                    {
                        "id": str(tx_seed_1),
                        "date": "2026-02-11",
                        "montant": "-122.00",
                        "devise": "CHF",
                        "payee": "Migros",
                        "libelle": "Migros Lausanne",
                        "category": "Alimentation",
                    },
                    {
                        "id": str(tx_seed_2),
                        "date": "2026-02-07",
                        "montant": "-2100.00",
                        "devise": "CHF",
                        "payee": "Régie Immo",
                        "libelle": "Loyer",
                        "category": "Logement",
                    },
                ], None
            return [
                {
                    "id": str(tx_seed_1),
                    "date": "2026-02-11",
                    "montant": "-122.00",
                    "devise": "CHF",
                    "payee": "Migros",
                    "libelle": "Migros Lausanne",
                },
                {
                    "id": str(tx_seed_2),
                    "date": "2026-02-07",
                    "montant": "-2100.00",
                    "devise": "CHF",
                    "payee": "Régie Immo",
                    "libelle": "Loyer",
                },
            ], None

    monkeypatch.setattr(agent_api, "SupabaseClient", _FakeSupabaseClient)

    response_start = client.post("/agent/chat", json={"message": "je veux lier compte pour mon foyer"}, headers=_auth_headers())
    assert response_start.status_code == 200

    response_type = client.post("/agent/chat", json={"message": "externe"}, headers=_auth_headers())
    assert response_type.status_code == 200

    response_label = client.post("/agent/chat", json={"message": "Conjoint"}, headers=_auth_headers())
    assert response_label.status_code == 200

    response_split = client.post("/agent/chat", json={"message": "60/40"}, headers=_auth_headers())
    assert response_split.status_code == 200
    reply = response_split.json()["reply"]
    assert "Voici les premières dépenses à valider" in reply
    assert "1)" in reply

    assert repo.upsert_household_link_calls == [
        {
            "profile_id": PROFILE_ID,
            "link_type": "external",
            "other_profile_id": None,
            "other_party_label": "Conjoint",
            "other_party_email": None,
            "default_split_ratio_other": "0.4000",
        }
    ]
    assert suggestions_repo.bulk_calls
    seeded_suggestions = suggestions_repo.bulk_calls[0]
    assert all(item.get("suggested_to_profile_id") is None for item in seeded_suggestions)
    assert all(item.get("other_party_label") == "Conjoint" for item in seeded_suggestions)
    active_task = repo.chat_state.get("active_task")
    assert isinstance(active_task, dict)
    assert active_task.get("type") == "shared_expense_confirm"


def test_agent_chat_account_link_setup_external_flow_no_candidates(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "get_user_from_bearer_token",
        lambda _token: {"id": str(AUTH_USER_ID), "email": "user@example.com"},
    )
    repo = _Repo()
    suggestions_repo = _SeedSuggestionsRepository()

    monkeypatch.setattr(agent_api, "get_profiles_repository", lambda: repo)
    monkeypatch.setattr(agent_api, "_get_shared_expenses_repository_or_501", lambda: suggestions_repo)
    monkeypatch.setattr(agent_api._config, "supabase_url", lambda: "http://localhost")
    monkeypatch.setattr(agent_api._config, "supabase_service_role_key", lambda: "service-role")
    monkeypatch.setattr(agent_api._config, "supabase_anon_key", lambda: "anon")

    class _FakeSupabaseClient:
        def __init__(self, settings):
            self.settings = settings

        def get_rows(self, *, table, query, with_count, use_anon_key):
            assert table == "releves_bancaires"
            return [], None

    monkeypatch.setattr(agent_api, "SupabaseClient", _FakeSupabaseClient)

    client.post("/agent/chat", json={"message": "je veux lier compte pour mon foyer"}, headers=_auth_headers())
    client.post("/agent/chat", json={"message": "externe"}, headers=_auth_headers())
    client.post("/agent/chat", json={"message": "Conjoint"}, headers=_auth_headers())
    response_split = client.post("/agent/chat", json={"message": "60/40"}, headers=_auth_headers())

    assert response_split.status_code == 200
    assert "pas trouvé de dépenses à proposer" in response_split.json()["reply"]
    assert repo.chat_state.get("active_task") is None


def test_inmemory_shared_expense_suggestions_dedup_uses_external_label() -> None:
    repository = InMemorySharedExpensesRepository()

    created = repository.create_shared_expense_suggestions_bulk(
        profile_id=PROFILE_ID,
        suggestions=[
            {
                "transaction_id": TX_ID_1,
                "suggested_to_profile_id": None,
                "suggested_split_ratio_other": Decimal("0.4"),
                "other_party_label": "Conjoint",
            },
            {
                "transaction_id": TX_ID_1,
                "suggested_to_profile_id": None,
                "suggested_split_ratio_other": Decimal("0.4"),
                "other_party_label": "Colocataire",
            },
        ],
    )

    assert created == 2
