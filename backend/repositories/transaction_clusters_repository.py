"""Repository adapter for transaction clusters and cluster actions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.db.supabase_client import SupabaseClient


class SupabaseTransactionClustersRepository:
    """Supabase-backed persistence for clustered transaction suggestions."""

    def __init__(self, *, client: SupabaseClient) -> None:
        self._client = client

    def upsert_cluster(
        self,
        *,
        profile_id: str,
        cluster_type: str,
        cluster_key: str,
        stats: dict[str, Any],
        transaction_ids: list[str],
    ) -> str:
        rows, _ = self._client.get_rows(
            table="transaction_clusters",
            query={
                "select": "id,status",
                "profile_id": f"eq.{profile_id}",
                "cluster_type": f"eq.{cluster_type}",
                "cluster_key": f"eq.{cluster_key}",
                "limit": 1,
            },
            with_count=False,
            use_anon_key=False,
        )

        current_status = str(rows[0].get("status") or "pending") if rows else "pending"
        next_status = "applied" if current_status == "applied" else "pending"

        upserted = self._client.upsert_row(
            table="transaction_clusters",
            on_conflict="profile_id,cluster_type,cluster_key",
            payload={
                "profile_id": profile_id,
                "cluster_type": cluster_type,
                "cluster_key": cluster_key,
                "stats": stats,
                "status": next_status,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            use_anon_key=False,
        )
        cluster_id = str(upserted[0]["id"])

        self._client.delete_rows(
            table="transaction_cluster_items",
            query={"cluster_id": f"eq.{cluster_id}"},
            use_anon_key=False,
        )

        if transaction_ids:
            self._client.post_rows(
                table="transaction_cluster_items",
                payload=[
                    {"cluster_id": cluster_id, "transaction_id": transaction_id}
                    for transaction_id in transaction_ids
                ],
                use_anon_key=False,
            )

        return cluster_id

    def list_clusters(
        self,
        *,
        profile_id: str,
        status: str = "pending",
        limit: int = 50,
        cluster_type: str | None = None,
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {
            "select": (
                "id,profile_id,cluster_type,cluster_key,stats,status,"
                "suggested_category_id,confidence,rationale,model,run_id,created_at,updated_at"
            ),
            "profile_id": f"eq.{profile_id}",
            "status": f"eq.{status}",
            "order": "updated_at.desc",
            "limit": limit,
        }
        if isinstance(cluster_type, str) and cluster_type.strip():
            query["cluster_type"] = f"eq.{cluster_type.strip()}"

        cluster_rows, _ = self._client.get_rows(
            table="transaction_clusters",
            query=query,
            with_count=False,
            use_anon_key=False,
        )

        if not cluster_rows:
            return []

        cluster_ids = [str(row["id"]) for row in cluster_rows]
        in_filter = f"in.({','.join(cluster_ids)})"
        item_rows, _ = self._client.get_rows(
            table="transaction_cluster_items",
            query={
                "select": "cluster_id,transaction_id",
                "cluster_id": in_filter,
            },
            with_count=False,
            use_anon_key=False,
        )

        items_by_cluster: dict[str, list[str]] = {cluster_id: [] for cluster_id in cluster_ids}
        for row in item_rows:
            key = str(row.get("cluster_id"))
            transaction_id = row.get("transaction_id")
            if transaction_id is None:
                continue
            if key not in items_by_cluster:
                items_by_cluster[key] = []
            items_by_cluster[key].append(str(transaction_id))

        result: list[dict[str, Any]] = []
        for row in cluster_rows:
            cluster_id = str(row["id"])
            stats = row.get("stats")
            stats_dict = stats if isinstance(stats, dict) else {}
            transaction_ids = items_by_cluster.get(cluster_id, [])

            result.append(
                {
                    **row,
                    "count": stats_dict.get("count"),
                    "sample_labels": stats_dict.get("sample_labels"),
                    "total_amount_abs": stats_dict.get("total_amount_abs"),
                    "items_count": len(transaction_ids),
                    "transaction_ids": transaction_ids,
                }
            )
        return result

    def apply_cluster_category(self, *, cluster_id: str, category_id: str, profile_id: str | None = None) -> None:
        normalized_profile_id = profile_id.strip() if isinstance(profile_id, str) and profile_id.strip() else None

        if normalized_profile_id:
            cluster_rows, _ = self._client.get_rows(
                table="transaction_clusters",
                query={
                    "select": "id,profile_id",
                    "id": f"eq.{cluster_id}",
                    "profile_id": f"eq.{normalized_profile_id}",
                    "limit": 1,
                },
                with_count=False,
                use_anon_key=False,
            )
            if not cluster_rows:
                raise ValueError("cluster_not_found_or_forbidden")

        items_query: dict[str, Any] = {
            "select": "transaction_id",
            "cluster_id": f"eq.{cluster_id}",
            "limit": 5000,
        }

        item_rows, _ = self._client.get_rows(
            table="transaction_cluster_items",
            query=items_query,
            with_count=False,
            use_anon_key=False,
        )

        transaction_ids = [str(row["transaction_id"]) for row in item_rows if row.get("transaction_id")]

        if transaction_ids:
            self._client.patch_rows(
                table="releves_bancaires",
                query=[("id", f"in.({','.join(transaction_ids)})")],
                payload={"category_id": category_id},
                use_anon_key=False,
            )

        clusters_query = {"id": f"eq.{cluster_id}"}
        if normalized_profile_id:
            clusters_query["profile_id"] = f"eq.{normalized_profile_id}"

        self._client.patch_rows(
            table="transaction_clusters",
            query=clusters_query,
            payload={
                "status": "applied",
                "suggested_category_id": category_id,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            use_anon_key=False,
        )

    def dismiss_cluster(self, *, cluster_id: str, profile_id: str | None = None) -> None:
        normalized_profile_id = profile_id.strip() if isinstance(profile_id, str) and profile_id.strip() else None

        if normalized_profile_id:
            cluster_rows, _ = self._client.get_rows(
                table="transaction_clusters",
                query={
                    "select": "id,profile_id",
                    "id": f"eq.{cluster_id}",
                    "profile_id": f"eq.{normalized_profile_id}",
                    "limit": 1,
                },
                with_count=False,
                use_anon_key=False,
            )
            if not cluster_rows:
                raise ValueError("cluster_not_found_or_forbidden")

        query = {"id": f"eq.{cluster_id}"}
        if normalized_profile_id:
            query["profile_id"] = f"eq.{normalized_profile_id}"

        self._client.patch_rows(
            table="transaction_clusters",
            query=query,
            payload={"status": "dismissed", "updated_at": datetime.now(timezone.utc).isoformat()},
            use_anon_key=False,
        )
