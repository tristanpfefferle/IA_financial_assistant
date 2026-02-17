"""Dedup/comparison logic adapted from legacy inserer_transactions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID


@dataclass(slots=True)
class DedupStats:
    new_rows: list[dict[str, object]]
    modified_rows: list[dict[str, object]]
    modified_existing_ids: list[UUID]
    identical_count: int
    duplicates_in_file: int
    ambiguous_matches_count: int


def _normalize_str(value: object) -> str:
    if isinstance(value, str):
        return value.strip().casefold()
    return ""


def _normalize_currency(value: object) -> str:
    normalized = _normalize_str(value)
    return normalized.upper() if normalized else "CHF"


def _signature(row: dict[str, object]) -> tuple[date, Decimal, str, str, UUID | None]:
    key_label = _normalize_str(row.get("libelle") or row.get("payee"))
    return (
        row["date"],
        row["montant"],
        _normalize_currency(row.get("devise")),
        key_label,
        row.get("bank_account_id"),
    )


def _content_key(row: dict[str, object]) -> tuple[date, Decimal, str, str, str]:
    return (
        row["date"],
        row["montant"],
        _normalize_currency(row.get("devise")),
        _normalize_str(row.get("libelle")),
        _normalize_str(row.get("payee")),
    )


def compare_rows(
    incoming_rows: list[dict[str, object]],
    existing_rows: list[dict[str, object]],
) -> DedupStats:
    existing_by_signature: dict[tuple[date, Decimal, str, str, UUID | None], list[dict[str, object]]] = {}
    for existing_row in existing_rows:
        signature = _signature(existing_row)
        existing_by_signature.setdefault(signature, []).append(existing_row)

    seen_file: set[tuple[date, Decimal, str, str, UUID | None]] = set()
    duplicates_in_file = 0
    new_rows: list[dict[str, object]] = []
    modified_rows: list[dict[str, object]] = []
    modified_existing_ids: list[UUID] = []
    identical_count = 0
    ambiguous_matches_count = 0

    for row in incoming_rows:
        sig = _signature(row)
        if sig in seen_file:
            duplicates_in_file += 1
            continue
        seen_file.add(sig)

        matching_existing = existing_by_signature.get(sig, [])
        if not matching_existing:
            new_rows.append(row)
            continue

        if len(matching_existing) > 1:
            ambiguous_matches_count += 1
            continue

        existing = matching_existing[0]

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
        ambiguous_matches_count=ambiguous_matches_count,
    )
