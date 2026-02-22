"""Releves import orchestrator (analyze/commit)."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from backend.repositories.profiles_repository import ProfilesRepository
from backend.repositories.releves_repository import RelevesRepository
from backend.services.classification.decision_engine import decide_releve_classification
from backend.services.releves_import.classification import classify_and_categorize_transaction
from backend.services.releves_import.dedup import compare_rows
from backend.services.releves_import.routing import route_bank_parser
from shared.models import (
    RelevesImportError,
    RelevesImportMode,
    RelevesImportModifiedAction,
    RelevesImportPreviewItem,
    RelevesImportRequest,
    RelevesImportResult,
)


@dataclass(slots=True)
class RelevesImportService:
    releves_repository: RelevesRepository
    profiles_repository: ProfilesRepository | None = None

    def _resolve_category_label(
        self,
        *,
        profile_id: UUID,
        category_id: UUID | None,
        fallback_label: str,
    ) -> str:
        if category_id is None or self.profiles_repository is None:
            return fallback_label
        categories = self.profiles_repository.list_profile_categories(profile_id=profile_id)
        for row in categories:
            if str(row.get("id") or "") == str(category_id):
                name = row.get("name")
                if isinstance(name, str) and name.strip():
                    return name.strip()
        return fallback_label

    @staticmethod
    def _extract_external_id(parsed_row: dict[str, object]) -> str | None:
        raw_meta = parsed_row.get("meta")
        if isinstance(raw_meta, dict):
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

        for key in ("no_transaction", "transaction_id"):
            value = parsed_row.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        return None

    def _normalize_row(
        self,
        *,
        profile_id: UUID,
        bank_account_id: UUID | None,
        parsed_row: dict[str, object],
        source: str,
    ) -> dict[str, object] | None:
        raw_date = parsed_row.get("date")
        if isinstance(raw_date, date):
            parsed_date = raw_date
        elif isinstance(raw_date, str) and raw_date:
            parsed_date = date.fromisoformat(raw_date[:10])
        else:
            return None

        raw_amount = parsed_row.get("montant")
        if isinstance(raw_amount, Decimal):
            amount = raw_amount
        elif raw_amount is None:
            return None
        else:
            amount = Decimal(str(raw_amount))

        external_id = self._extract_external_id(parsed_row)
        raw_meta = parsed_row.get("meta")
        meta_dict: dict[str, Any] = dict(raw_meta) if isinstance(raw_meta, dict) else {}
        if external_id is not None:
            meta_dict["_external_id"] = external_id
            meta_dict["_external_source"] = source

        raw_dict: dict[str, Any] | None = dict(raw_meta) if isinstance(raw_meta, dict) else None

        classification = classify_and_categorize_transaction(
            {
                "montant": amount,
                "payee": parsed_row.get("payee"),
                "libelle": parsed_row.get("libelle"),
            }
        )
        meta_dict["category_key"] = classification.category_key
        meta_dict["category_status"] = classification.category_status
        meta_dict["tx_kind"] = classification.tx_kind

        decision = None
        if self.profiles_repository is not None:
            decision = decide_releve_classification(
                profile_id=profile_id,
                bank_account_id=bank_account_id,
                libelle=str(parsed_row.get("libelle") or "") or None,
                payee=str(parsed_row.get("payee") or "") or None,
                montant=amount,
                devise=str(parsed_row.get("devise") or "CHF"),
                date=parsed_date,
                metadata=meta_dict,
                repositories=self.profiles_repository,
            )
            meta_dict["classification_source"] = decision.source.value
            meta_dict["classification_rationale"] = decision.rationale
            meta_dict["classify_confidence"] = decision.confidence
            meta_dict["classify_at"] = datetime.now(timezone.utc).isoformat()

        category_label = (
            self._resolve_category_label(
                profile_id=profile_id,
                category_id=(decision.category_id if decision else None),
                fallback_label=classification.category_label,
            )
            if decision is not None
            else classification.category_label
        )

        return {
            "profile_id": profile_id,
            "bank_account_id": bank_account_id,
            "date": parsed_date,
            "montant": amount,
            "devise": str(parsed_row.get("devise") or "CHF"),
            "libelle": parsed_row.get("libelle"),
            "payee": parsed_row.get("payee"),
            "categorie": parsed_row.get("categorie") or category_label,
            "merchant_entity_id": decision.merchant_entity_id if decision else None,
            "category_id": decision.category_id if decision else None,
            "meta": meta_dict,
            "contenu_brut": raw_dict,
            "source": source,
        }

    def import_releves(self, request: RelevesImportRequest) -> RelevesImportResult:
        errors: list[RelevesImportError] = []
        normalized_rows: list[dict[str, object]] = []

        for file in request.files:
            try:
                content = base64.b64decode(file.content_base64)
                source, parsed_rows = route_bank_parser(file.filename, content)
            except Exception as exc:
                errors.append(RelevesImportError(file=file.filename, message=str(exc)))
                continue

            for index, parsed_row in enumerate(parsed_rows):
                try:
                    normalized = self._normalize_row(
                        profile_id=request.profile_id,
                        bank_account_id=request.bank_account_id,
                        parsed_row=parsed_row,
                        source=source,
                    )
                except Exception as exc:
                    errors.append(
                        RelevesImportError(file=file.filename, row_index=index, message=str(exc))
                    )
                    continue

                if normalized is None:
                    errors.append(
                        RelevesImportError(
                            file=file.filename,
                            row_index=index,
                            message="Ligne incomplète (date/montant).",
                        )
                    )
                    continue
                normalized_rows.append(normalized)

        existing_rows = self.releves_repository.list_releves_for_import(
            profile_id=request.profile_id,
            bank_account_id=None,
        )
        dedup = compare_rows(normalized_rows, existing_rows)

        if dedup.ambiguous_matches_count:
            errors.append(
                RelevesImportError(
                    file="dedup",
                    message=(
                        f"{dedup.ambiguous_matches_count} correspondances ambiguës; "
                        "remplacement non appliqué."
                    ),
                )
            )

        rows_to_insert = list(dedup.new_rows)
        replaced_count = 0

        if request.import_mode == RelevesImportMode.COMMIT:
            if request.modified_action == RelevesImportModifiedAction.REPLACE and dedup.modified_rows:
                self.releves_repository.delete_releves_by_ids(
                    profile_id=request.profile_id,
                    releve_ids=dedup.modified_existing_ids,
                )
                rows_to_insert.extend(dedup.modified_rows)
                replaced_count = len(dedup.modified_rows)

            imported_count = self.releves_repository.insert_releves_bulk(
                profile_id=request.profile_id,
                rows=rows_to_insert,
            ) if rows_to_insert else 0
        else:
            imported_count = 0

        preview = [
            RelevesImportPreviewItem(
                date=row["date"],
                montant=row["montant"],
                devise=str(row.get("devise") or "CHF"),
                libelle=row.get("libelle"),
                payee=row.get("payee"),
                categorie=row.get("categorie"),
                bank_account_id=row.get("bank_account_id"),
            )
            for row in normalized_rows[:20]
        ]

        return RelevesImportResult(
            imported_count=imported_count,
            failed_count=len(errors),
            duplicates_count=dedup.identical_count + dedup.duplicates_in_file,
            replaced_count=replaced_count,
            identical_count=dedup.identical_count,
            modified_count=len(dedup.modified_rows),
            new_count=len(dedup.new_rows),
            requires_confirmation=(
                request.import_mode == RelevesImportMode.ANALYZE
                and (len(dedup.new_rows) > 0 or len(dedup.modified_rows) > 0)
            ),
            errors=errors,
            preview=preview,
        )
