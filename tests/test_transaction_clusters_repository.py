"""Smoke tests for transaction clusters repository orchestration."""

from __future__ import annotations

from backend.repositories.transaction_clusters_repository import SupabaseTransactionClustersRepository


class _ClientStub:
    def __init__(
        self,
        *,
        get_responses: list[list[dict[str, object]]] | None = None,
        upsert_responses: list[list[dict[str, object]]] | None = None,
    ) -> None:
        self._get_responses = get_responses or []
        self._upsert_responses = upsert_responses or []
        self.get_calls: list[dict[str, object]] = []
        self.upsert_calls: list[dict[str, object]] = []
        self.patch_calls: list[dict[str, object]] = []
        self.post_calls: list[dict[str, object]] = []
        self.delete_calls: list[dict[str, object]] = []

    def get_rows(self, *, table, query, with_count, use_anon_key=False):
        self.get_calls.append(
            {
                "table": table,
                "query": query,
                "with_count": with_count,
                "use_anon_key": use_anon_key,
            }
        )
        call_index = len(self.get_calls) - 1
        rows = self._get_responses[call_index] if call_index < len(self._get_responses) else []
        return rows, None

    def upsert_row(self, *, table, payload, on_conflict, use_anon_key=False):
        self.upsert_calls.append(
            {
                "table": table,
                "payload": payload,
                "on_conflict": on_conflict,
                "use_anon_key": use_anon_key,
            }
        )
        call_index = len(self.upsert_calls) - 1
        if call_index < len(self._upsert_responses):
            return self._upsert_responses[call_index]
        return []

    def patch_rows(self, *, table, query, payload, use_anon_key=False):
        self.patch_calls.append(
            {
                "table": table,
                "query": query,
                "payload": payload,
                "use_anon_key": use_anon_key,
            }
        )
        return []

    def post_rows(self, *, table, payload, use_anon_key=False, prefer="return=representation"):
        self.post_calls.append(
            {
                "table": table,
                "payload": payload,
                "use_anon_key": use_anon_key,
                "prefer": prefer,
            }
        )
        return []

    def delete_rows(self, *, table, query, use_anon_key=False):
        self.delete_calls.append(
            {
                "table": table,
                "query": query,
                "use_anon_key": use_anon_key,
            }
        )
        return []


def test_upsert_cluster_preserves_applied_status_and_refreshes_items() -> None:
    client = _ClientStub(
        get_responses=[[{"id": "cluster-1", "status": "applied"}]],
        upsert_responses=[[{"id": "cluster-1"}]],
    )
    repository = SupabaseTransactionClustersRepository(client=client)

    cluster_id = repository.upsert_cluster(
        profile_id="profile-1",
        cluster_type="recurring",
        cluster_key="key-1",
        stats={"count": 3},
        transaction_ids=["tx-1", "tx-2"],
    )

    assert cluster_id == "cluster-1"
    assert client.upsert_calls[0]["table"] == "transaction_clusters"
    assert client.upsert_calls[0]["on_conflict"] == "profile_id,cluster_type,cluster_key"
    assert client.upsert_calls[0]["payload"]["status"] == "applied"
    assert client.delete_calls == [
        {
            "table": "transaction_cluster_items",
            "query": {"cluster_id": "eq.cluster-1"},
            "use_anon_key": False,
        }
    ]
    assert client.post_calls[0]["table"] == "transaction_cluster_items"
    assert client.post_calls[0]["payload"] == [
        {"cluster_id": "cluster-1", "transaction_id": "tx-1"},
        {"cluster_id": "cluster-1", "transaction_id": "tx-2"},
    ]


def test_list_clusters_returns_stats_fields_and_items_count() -> None:
    client = _ClientStub(
        get_responses=[
            [
                {
                    "id": "cluster-1",
                    "profile_id": "profile-1",
                    "cluster_type": "recurring",
                    "cluster_key": "k1",
                    "stats": {
                        "count": 4,
                        "sample_labels": ["NETFLIX", "Spotify"],
                        "total_amount_abs": "123.45",
                    },
                    "status": "pending",
                    "suggested_category_id": None,
                    "confidence": 0.8,
                    "rationale": None,
                    "model": None,
                    "run_id": None,
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-02T00:00:00+00:00",
                }
            ],
            [
                {"cluster_id": "cluster-1", "transaction_id": "tx-1"},
                {"cluster_id": "cluster-1", "transaction_id": "tx-2"},
            ],
        ]
    )
    repository = SupabaseTransactionClustersRepository(client=client)

    rows = repository.list_clusters(profile_id="profile-1", status="pending", limit=20)

    assert len(rows) == 1
    assert rows[0]["count"] == 4
    assert rows[0]["sample_labels"] == ["NETFLIX", "Spotify"]
    assert rows[0]["total_amount_abs"] == "123.45"
    assert rows[0]["items_count"] == 2
    assert rows[0]["transaction_ids"] == ["tx-1", "tx-2"]


def test_apply_cluster_category_updates_transactions_and_cluster() -> None:
    client = _ClientStub(
        get_responses=[
            [{"id": "cluster-1", "profile_id": "profile-1"}],
            [{"transaction_id": "tx-1"}, {"transaction_id": "tx-2"}],
        ],
    )
    repository = SupabaseTransactionClustersRepository(client=client)

    repository.apply_cluster_category(cluster_id="cluster-1", category_id="cat-1", profile_id="profile-1")

    assert client.get_calls[0]["table"] == "transaction_clusters"
    assert client.get_calls[0]["query"] == {
        "select": "id,profile_id",
        "id": "eq.cluster-1",
        "profile_id": "eq.profile-1",
        "limit": 1,
    }
    assert client.get_calls[1]["table"] == "transaction_cluster_items"
    assert client.get_calls[1]["query"] == {
        "select": "transaction_id",
        "cluster_id": "eq.cluster-1",
        "limit": 5000,
    }

    assert len(client.patch_calls) == 2
    assert client.patch_calls[0]["table"] == "releves_bancaires"
    assert client.patch_calls[0]["query"] == [("id", "in.(tx-1,tx-2)")]
    assert client.patch_calls[0]["payload"] == {"category_id": "cat-1"}
    assert client.patch_calls[1]["table"] == "transaction_clusters"
    assert client.patch_calls[1]["query"] == {"id": "eq.cluster-1", "profile_id": "eq.profile-1"}
    assert client.patch_calls[1]["payload"]["status"] == "applied"
    assert client.patch_calls[1]["payload"]["suggested_category_id"] == "cat-1"


def test_dismiss_cluster_marks_cluster_as_dismissed() -> None:
    client = _ClientStub(get_responses=[[{"id": "cluster-1", "profile_id": "profile-1"}]])
    repository = SupabaseTransactionClustersRepository(client=client)

    repository.dismiss_cluster(cluster_id="cluster-1", profile_id="profile-1")

    assert client.get_calls[0]["table"] == "transaction_clusters"
    assert client.get_calls[0]["query"] == {
        "select": "id,profile_id",
        "id": "eq.cluster-1",
        "profile_id": "eq.profile-1",
        "limit": 1,
    }
    assert len(client.patch_calls) == 1
    assert client.patch_calls[0]["table"] == "transaction_clusters"
    assert client.patch_calls[0]["query"] == {"id": "eq.cluster-1", "profile_id": "eq.profile-1"}
    assert client.patch_calls[0]["payload"]["status"] == "dismissed"


def test_apply_cluster_category_raises_when_cluster_profile_mismatch() -> None:
    client = _ClientStub(get_responses=[[]])
    repository = SupabaseTransactionClustersRepository(client=client)

    try:
        repository.apply_cluster_category(cluster_id="cluster-1", category_id="cat-1", profile_id="profile-1")
    except ValueError as exc:
        assert str(exc) == "cluster_not_found_or_forbidden"
    else:
        raise AssertionError("ValueError not raised")
