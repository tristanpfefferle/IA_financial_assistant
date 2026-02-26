"""Repository for persisted async import jobs and events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from backend.db.supabase_client import SupabaseClient


@dataclass(slots=True)
class ImportJobRow:
    id: UUID
    profile_id: UUID
    status: str
    created_at: datetime | None
    updated_at: datetime | None
    error_message: str | None
    total_transactions: int | None
    processed_transactions: int | None
    total_llm_items: int | None
    processed_llm_items: int | None


@dataclass(slots=True)
class ImportJobEventRow:
    id: int
    job_id: UUID
    seq: int
    kind: str
    message: str
    progress: float | None
    payload: dict[str, Any] | None
    created_at: datetime | None


class SupabaseImportJobsRepository:
    """Supabase adapter for async import jobs."""

    def __init__(self, *, client: SupabaseClient) -> None:
        self._client = client

    def create_job(self, *, profile_id: UUID) -> UUID:
        rows = self._client.post_rows(
            table="import_jobs",
            payload={"profile_id": str(profile_id), "status": "pending"},
            use_anon_key=False,
        )
        return UUID(str(rows[0]["id"]))

    def get_job(self, *, profile_id: UUID, job_id: UUID) -> ImportJobRow | None:
        rows, _ = self._client.get_rows(
            table="import_jobs",
            query={"select": "*", "profile_id": f"eq.{profile_id}", "id": f"eq.{job_id}", "limit": 1},
            with_count=False,
            use_anon_key=False,
        )
        if not rows:
            return None
        return self._map_job(rows[0])

    def patch_job(self, *, profile_id: UUID, job_id: UUID, payload: dict[str, Any]) -> None:
        data = dict(payload)
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._client.patch_rows(
            table="import_jobs",
            query={"profile_id": f"eq.{profile_id}", "id": f"eq.{job_id}"},
            payload=data,
            use_anon_key=False,
        )

    def next_event_seq(self, *, job_id: UUID) -> int:
        rows, _ = self._client.get_rows(
            table="import_job_events",
            query={"select": "seq", "job_id": f"eq.{job_id}", "order": "seq.desc", "limit": 1},
            with_count=False,
            use_anon_key=False,
        )
        return int(rows[0]["seq"]) + 1 if rows else 1

    def create_event(self, *, job_id: UUID, seq: int, kind: str, message: str, progress: float | None, payload: dict[str, Any] | None) -> ImportJobEventRow:
        rows = self._client.post_rows(
            table="import_job_events",
            payload={
                "job_id": str(job_id),
                "seq": seq,
                "kind": kind,
                "message": message,
                "progress": progress,
                "payload": payload,
            },
            use_anon_key=False,
        )
        return self._map_event(rows[0])

    def list_events_since(self, *, job_id: UUID, after_seq: int, limit: int = 200) -> list[ImportJobEventRow]:
        rows, _ = self._client.get_rows(
            table="import_job_events",
            query={
                "select": "*",
                "job_id": f"eq.{job_id}",
                "seq": f"gt.{after_seq}",
                "order": "seq.asc",
                "limit": limit,
            },
            with_count=False,
            use_anon_key=False,
        )
        return [self._map_event(row) for row in rows]

    @staticmethod
    def _map_job(row: dict[str, Any]) -> ImportJobRow:
        return ImportJobRow(
            id=UUID(str(row["id"])),
            profile_id=UUID(str(row["profile_id"])),
            status=str(row["status"]),
            created_at=_parse_datetime(row.get("created_at")),
            updated_at=_parse_datetime(row.get("updated_at")),
            error_message=row.get("error_message"),
            total_transactions=_to_int_or_none(row.get("total_transactions")),
            processed_transactions=_to_int_or_none(row.get("processed_transactions")),
            total_llm_items=_to_int_or_none(row.get("total_llm_items")),
            processed_llm_items=_to_int_or_none(row.get("processed_llm_items")),
        )

    @staticmethod
    def _map_event(row: dict[str, Any]) -> ImportJobEventRow:
        payload = row.get("payload")
        return ImportJobEventRow(
            id=int(row["id"]),
            job_id=UUID(str(row["job_id"])),
            seq=int(row["seq"]),
            kind=str(row["kind"]),
            message=str(row["message"]),
            progress=float(payload_progress) if (payload_progress := row.get("progress")) is not None else None,
            payload=payload if isinstance(payload, dict) else None,
            created_at=_parse_datetime(row.get("created_at")),
        )


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
