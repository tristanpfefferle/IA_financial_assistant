"""Dedup/comparison logic adapted from legacy inserer_transactions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID


@dataclass(slots=True)
class DedupStats:
    new_rows: list[dict[str, object]]
    modified_rows: list[dict[str, object]]
    modified_existing_ids: list[UUID]
    identical_count: int
    duplicates_in_file: int


def _signature(row: dict[str, object]) -> tuple[date, str | None, UUID | None]:
    return (
        row["date"],
        row.get("payee"),
        row.get("bank_account_id"),
    )


def _content_key(row: dict[str, object]) -> tuple[date, Decimal, str | None, str | None, str]:
    return (
        row["date"],
        row["montant"],
        row.get("libelle"),
        row.get("payee"),
        row.get("devise", "CHF"),
    )


def compare_rows(
    incoming_rows: list[dict[str, object]],
    existing_rows: list[dict[str, object]],
) -> DedupStats:
    existing_by_signature: dict[tuple[date, str | None, UUID | None], dict[str, object]] = {
        _signature(row): row for row in existing_rows
    }

    seen_file: set[tuple[date, str | None, UUID | None]] = set()
    duplicates_in_file = 0
    new_rows: list[dict[str, object]] = []
    modified_rows: list[dict[str, object]] = []
    modified_existing_ids: list[UUID] = []
    identical_count = 0

    for row in incoming_rows:
        sig = _signature(row)
        if sig in seen_file:
            duplicates_in_file += 1
            continue
        seen_file.add(sig)

        existing = existing_by_signature.get(sig)
        if existing is None:
            new_rows.append(row)
            continue

        if _content_key(existing) == _content_key(row):
            identical_count += 1
            continue

        modified_rows.append(row)
        existing_id = existing.get("id")
        if isinstance(existing_id, UUID):
            modified_existing_ids.append(existing_id)

    return DedupStats(
        new_rows=new_rows,
        modified_rows=modified_rows,
        modified_existing_ids=modified_existing_ids,
        identical_count=identical_count,
        duplicates_in_file=duplicates_in_file,
    )
