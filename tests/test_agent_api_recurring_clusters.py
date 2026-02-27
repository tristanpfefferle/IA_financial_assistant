"""Tests for recurring clusters endpoints in agent.api."""

from uuid import UUID

from fastapi.testclient import TestClient

import agent.api as agent_api
from agent.api import app


client = TestClient(app)
PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
CLUSTER_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
CATEGORY_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def _mock_authenticated(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_api,
        "_resolve_authenticated_profile",
        lambda _request, _authorization=None: (UUID(int=1), PROFILE_ID),
    )


def test_list_recurring_clusters_returns_501_without_supabase(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)
    monkeypatch.setattr(agent_api._config, "supabase_url", lambda: "")
    monkeypatch.setattr(agent_api._config, "supabase_service_role_key", lambda: "")

    response = client.get("/clusters/recurring", headers=_auth_headers())

    assert response.status_code == 501
    assert response.json()["detail"] == "transaction clusters disabled"


def test_list_recurring_clusters_returns_items(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _FakeRepository:
        def list_clusters(self, *, profile_id: str, status: str = "pending", limit: int = 50, cluster_type: str | None = None):
            assert profile_id == str(PROFILE_ID)
            assert status == "pending"
            assert limit == 50
            assert cluster_type == "recurring"
            return [
                {
                    "id": str(CLUSTER_ID),
                    "cluster_type": "recurring",
                    "cluster_key": "netflix",
                    "status": "pending",
                    "count": 3,
                    "total_amount_abs": "89.70",
                    "sample_labels": ["NETFLIX.COM"],
                    "items_count": 3,
                    "transaction_ids": ["tx-1", "tx-2", "tx-3"],
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-02T00:00:00+00:00",
                },
                {
                    "id": "ignored",
                    "cluster_type": "salary",
                    "cluster_key": "salary",
                    "status": "pending",
                },
            ]

    monkeypatch.setattr(agent_api, "_get_transaction_clusters_repository_or_501", lambda: _FakeRepository())

    response = client.get("/clusters/recurring", headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["type"] == "clusters_list"
    assert len(payload["items"]) == 1
    assert payload["items"][0]["id"] == str(CLUSTER_ID)
    assert payload["items"][0]["cluster_type"] == "recurring"


def test_apply_recurring_cluster_calls_repository(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)
    called: dict[str, str] = {}

    class _FakeRepository:
        def apply_cluster_category(self, *, cluster_id: str, category_id: str, profile_id: str | None = None) -> None:
            called["cluster_id"] = cluster_id
            called["category_id"] = category_id
            called["profile_id"] = str(profile_id)

    monkeypatch.setattr(agent_api, "_get_transaction_clusters_repository_or_501", lambda: _FakeRepository())

    response = client.post(
        f"/clusters/{CLUSTER_ID}/apply",
        headers=_auth_headers(),
        json={"category_id": str(CATEGORY_ID)},
    )

    assert response.status_code == 200
    assert response.json() == {
        "type": "cluster_applied",
        "cluster_id": str(CLUSTER_ID),
        "category_id": str(CATEGORY_ID),
    }
    assert called == {
        "cluster_id": str(CLUSTER_ID),
        "category_id": str(CATEGORY_ID),
        "profile_id": str(PROFILE_ID),
    }


def test_apply_recurring_cluster_returns_404_when_cluster_not_found(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _FakeRepository:
        def apply_cluster_category(self, *, cluster_id: str, category_id: str, profile_id: str | None = None) -> None:
            raise ValueError("cluster_not_found_or_forbidden")

    monkeypatch.setattr(agent_api, "_get_transaction_clusters_repository_or_501", lambda: _FakeRepository())

    response = client.post(
        f"/clusters/{CLUSTER_ID}/apply",
        headers=_auth_headers(),
        json={"category_id": str(CATEGORY_ID)},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "cluster not found"}


def test_dismiss_recurring_cluster_calls_repository(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)
    called: dict[str, str] = {}

    class _FakeRepository:
        def dismiss_cluster(self, *, cluster_id: str, profile_id: str | None = None) -> None:
            called["cluster_id"] = cluster_id
            called["profile_id"] = str(profile_id)

    monkeypatch.setattr(agent_api, "_get_transaction_clusters_repository_or_501", lambda: _FakeRepository())

    response = client.post(f"/clusters/{CLUSTER_ID}/dismiss", headers=_auth_headers())

    assert response.status_code == 200
    assert response.json() == {
        "type": "cluster_dismissed",
        "cluster_id": str(CLUSTER_ID),
    }
    assert called == {
        "cluster_id": str(CLUSTER_ID),
        "profile_id": str(PROFILE_ID),
    }


def test_dismiss_recurring_cluster_returns_404_when_cluster_not_found(monkeypatch) -> None:
    _mock_authenticated(monkeypatch)

    class _FakeRepository:
        def dismiss_cluster(self, *, cluster_id: str, profile_id: str | None = None) -> None:
            raise ValueError("cluster_not_found_or_forbidden")

    monkeypatch.setattr(agent_api, "_get_transaction_clusters_repository_or_501", lambda: _FakeRepository())

    response = client.post(f"/clusters/{CLUSTER_ID}/dismiss", headers=_auth_headers())

    assert response.status_code == 404
    assert response.json() == {"detail": "cluster not found"}
