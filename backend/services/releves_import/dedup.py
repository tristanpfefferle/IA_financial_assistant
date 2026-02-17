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


def _normalize_source(value: object) -> str:
    return _normalize_str(value)


def _content_key(row: dict[str, object]) -> tuple[date, Decimal, str, str, str]:
    return (
        row["date"],
        row["montant"],
        _normalize_currency(row.get("devise")),
        _normalize_str(row.get("libelle")),
        _normalize_str(row.get("payee")),
    )


def _fallback_match_key(row: dict[str, object]) -> tuple[date, UUID | None, str, str]:
    return (
        row["date"],
        row.get("bank_account_id"),
        _normalize_currency(row.get("devise")),
        _normalize_str(row.get("libelle") or row.get("payee")),
    )


def _extract_external_id(row: dict[str, object]) -> str | None:
    raw_meta = row.get("meta")
    if isinstance(raw_meta, dict):
        metadata_external_id = raw_meta.get("_external_id")
        if isinstance(metadata_external_id, str) and metadata_external_id.strip():
            return metadata_external_id.strip()

        for key in (
            "No de transaction",
            "No de transaction;",
            "No de transaction ",
            "No. de transaction",
            "no de transaction",
        ):
            value = raw_meta.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    explicit_external_id = row.get("external_id")
    if isinstance(explicit_external_id, str) and explicit_external_id.strip():
        return explicit_external_id.strip()

    return None


def _external_match_key(row: dict[str, object], external_id: str) -> tuple[str, str]:
    return (_normalize_source(row.get("source")), external_id)


def compare_rows(
    incoming_rows: list[dict[str, object]],
    existing_rows: list[dict[str, object]],
) -> DedupStats:
    existing_by_external_id: dict[tuple[str, str], list[dict[str, object]]] = {}
    existing_by_fallback: dict[tuple[date, UUID | None, str, str], list[dict[str, object]]] = {}

    for existing_row in existing_rows:
        existing_external_id = _extract_external_id(existing_row)
        if existing_external_id is not None:
            external_key = _external_match_key(existing_row, existing_external_id)
            existing_by_external_id.setdefault(external_key, []).append(existing_row)

        fallback_key = _fallback_match_key(existing_row)
        existing_by_fallback.setdefault(fallback_key, []).append(existing_row)

    seen_file_content: set[tuple[date, Decimal, str, str, str]] = set()
    duplicates_in_file = 0
    new_rows: list[dict[str, object]] = []
    modified_rows: list[dict[str, object]] = []
    modified_existing_ids: list[UUID] = []
    identical_count = 0
    ambiguous_matches_count = 0

    for row in incoming_rows:
        row_content_key = _content_key(row)
        if row_content_key in seen_file_content:
            duplicates_in_file += 1
            continue
        seen_file_content.add(row_content_key)

        matching_existing: list[dict[str, object]]
        row_external_id = _extract_external_id(row)

        if row_external_id is not None:
            matching_existing = existing_by_external_id.get(_external_match_key(row, row_external_id), [])
            if not matching_existing:
                matching_existing = existing_by_fallback.get(_fallback_match_key(row), [])
        else:
            matching_existing = existing_by_fallback.get(_fallback_match_key(row), [])

        if not matching_existing:
            new_rows.append(row)
            continue

        if len(matching_existing) > 1:
            ambiguous_matches_count += 1
            continue

        existing = matching_existing[0]

        if _content_key(existing) == row_content_key:
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
