"""Releves import orchestrator (analyze/commit)."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from backend.repositories.profiles_repository import ProfilesRepository
from backend.repositories.releves_repository import RelevesRepository
from backend.services.classification.decision_engine import decide_releve_classification, normalize_merchant_alias
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

    @staticmethod
    def _fallback_autres_category_id(*, profile_id: UUID) -> UUID:
        """Build a deterministic fallback category id when no profiles repository is configured."""

        return uuid5(NAMESPACE_URL, f"ia-financial-assistant:{profile_id}:category:autres")

    def _resolve_default_category_id(self, *, profile_id: UUID) -> UUID:
        """Resolve the mandatory fallback category id (`Autres`) for a profile."""

        if self.profiles_repository is None:
            return self._fallback_autres_category_id(profile_id=profile_id)

        self.profiles_repository.ensure_system_categories(
            profile_id=profile_id,
            categories=[{"system_key": "other", "name": "Autres"}],
        )
        category_id = self.profiles_repository.find_profile_category_id_by_name_norm(
            profile_id=profile_id,
            name_norm="autres",
        )
        if category_id is None:
            raise RuntimeError("Impossible de résoudre la catégorie système obligatoire 'Autres'.")
        return category_id

    @staticmethod
    def _redact_llm_context_value(value: str | None, *, max_len: int = 200) -> str | None:
        if value is None:
            return None

        cleaned = " ".join(str(value).split())
        if not cleaned:
            return None

        cleaned = re.sub(r"\bCH\d{2}[0-9A-Z ]{10,}\b", "[REDACTED_IBAN]", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"(?:\+41|0041)\s?(?:\(?0\)?\s?)?(?:\d[\s.-]?){8,12}", "[REDACTED_PHONE]", cleaned)
        return cleaned[:max_len]

    def _build_non_sensitive_llm_context(
        self,
        *,
        source: str,
        parsed_date: date,
        amount: Decimal,
        devise: str,
        payee: str | None,
        libelle: str | None,
        external_id: str | None,
        bank_account_id: UUID | None,
    ) -> dict[str, str]:
        llm_context: dict[str, str] = {
            "source": source,
            "date": parsed_date.isoformat(),
            "amount": str(amount),
            "currency": devise,
        }
        optional_fields = {
            "payee": self._redact_llm_context_value(payee),
            "libelle": self._redact_llm_context_value(libelle),
            "external_id": self._redact_llm_context_value(external_id),
            "bank_account_id": str(bank_account_id) if bank_account_id is not None else None,
        }
        for key, value in optional_fields.items():
            if value:
                llm_context[key] = value
        return llm_context

    @staticmethod
    def _fallback_merchant_entity_id(*, profile_id: UUID, merchant_key_norm: str) -> UUID:
        """Build a deterministic merchant entity id fallback when repository is unavailable."""

        return uuid5(
            NAMESPACE_URL,
            f"ia-financial-assistant:{profile_id}:merchant:{merchant_key_norm}",
        )

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

    def _build_observed_alias_key_norm(self, observed_alias: str) -> str:
        """Build a stable normalized key dedicated to map-alias suggestion deduplication."""

        normalized = normalize_merchant_alias(observed_alias.lower())
        if not normalized:
            return ""

        stopwords = {
            "paiement",
            "payment",
            "carte",
            "debit",
            "credit",
            "transaction",
            "no",
            "numero",
            "sumup",
            "twint",
            "paypal",
            "num",
        }
        has_aggregator = any(token in normalized.split() for token in ("sumup", "twint", "paypal"))

        cleaned = re.sub(r"\b\d{1,2}[/.]\d{1,2}\b", " ", normalized)
        cleaned = re.sub(r"\b\d{1,2}\s+\d{1,2}\b", " ", cleaned)
        cleaned = re.sub(r"\[(?:num|numero)\]", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b\d{5,}\s+\d+\b", " ", cleaned)
        cleaned = re.sub(r"\b\d{5,}\b", " ", cleaned)
        cleaned = re.sub(r"\s-\s", " ", cleaned)

        raw_tokens = [token.strip("*_-.") for token in cleaned.split()]

        compacted_tokens: list[str] = []
        idx = 0
        while idx < len(raw_tokens):
            current = raw_tokens[idx]
            nxt = raw_tokens[idx + 1] if idx + 1 < len(raw_tokens) else ""
            if current == "l" and nxt == "e":
                compacted_tokens.append("le")
                idx += 2
                continue
            compacted_tokens.append(current)
            idx += 1

        tokens: list[str] = []
        for token in compacted_tokens:
            if not token or token == "-":
                continue
            if token in stopwords:
                continue
            if token.isdigit() and (len(token) >= 4 or (has_aggregator and len(token) <= 2)):
                continue
            tokens.append(token)

        return " ".join(tokens).strip()

    def _derive_clean_merchant_key_norm(self, observed_alias: str) -> str:
        """Derive a concise merchant key from the stable alias key."""

        alias_key = self._build_observed_alias_key_norm(observed_alias)
        if not alias_key:
            return "unknown"

        tokens = alias_key.split()
        cleaned_tokens: list[str] = []
        encountered_numeric_hint = False
        for token in tokens:
            if token.isdigit() and len(token) == 4:
                encountered_numeric_hint = True
                continue
            if encountered_numeric_hint:
                continue
            cleaned_tokens.append(token)

        if not cleaned_tokens:
            return "unknown"

        return " ".join(cleaned_tokens[:3]).strip() or "unknown"

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

        payee = str(parsed_row.get("payee") or "").strip() or None
        libelle = str(parsed_row.get("libelle") or "").strip() or None
        devise = str(parsed_row.get("devise") or "CHF")

        observed_alias = str(payee or libelle or "").strip()
        observed_alias_norm = normalize_merchant_alias(observed_alias)
        observed_alias_key_norm = self._build_observed_alias_key_norm(observed_alias)
        merchant_key_norm = self._derive_clean_merchant_key_norm(observed_alias)
        meta_dict["observed_alias_key_norm"] = observed_alias_key_norm

        decision = None
        suggestion_created = False
        if self.profiles_repository is not None:
            resolved_entity = None
            if observed_alias_norm:
                resolved_entity = self.profiles_repository.find_merchant_entity_by_alias_norm(
                    alias_norm=observed_alias_norm,
                )

            if resolved_entity is not None and resolved_entity.get("id"):
                merchant_entity_id = UUID(str(resolved_entity["id"]))
                decision = decide_releve_classification(
                    profile_id=profile_id,
                    merchant_entity_id=merchant_entity_id,
                    bank_account_id=bank_account_id,
                    libelle=libelle,
                    payee=payee,
                    montant=amount,
                    devise=devise,
                    date=parsed_date,
                    metadata=meta_dict,
                    repositories=self.profiles_repository,
                )
                meta_dict["classification_source"] = decision.source.value
                meta_dict["classification_rationale"] = decision.rationale
                meta_dict["classify_confidence"] = decision.confidence
                meta_dict["classify_at"] = datetime.now(timezone.utc).isoformat()
            else:
                merchant_entity_id = None
                if observed_alias_norm:
                    meta_dict["merchant_resolution"] = "unresolved"
                    meta_dict["observed_alias_norm"] = observed_alias_norm
                    meta_dict["observed_alias_key_norm"] = observed_alias_key_norm
                    meta_dict["llm_context"] = self._build_non_sensitive_llm_context(
                        source=source,
                        parsed_date=parsed_date,
                        amount=amount,
                        devise=devise,
                        payee=payee,
                        libelle=libelle,
                        external_id=external_id,
                        bank_account_id=bank_account_id,
                    )
                    # TODO(tristanpfefferle): Le batch job LLM transformera les suggestions
                    # map_alias en merchant_entity + alias + suggested_category_norm.
                    suggestion_created = self.profiles_repository.create_pending_map_alias_suggestion(
                        profile_id=profile_id,
                        observed_alias=observed_alias,
                        observed_alias_norm=observed_alias_key_norm,
                        rationale=(
                            "Alias inconnu lors de l'import; nécessite normalisation/"
                            "canonicalisation et catégorisation LLM."
                        ),
                        confidence=0.0,
                    )
                else:
                    meta_dict["merchant_resolution"] = "unresolved_empty_alias"
        else:
            merchant_entity_id = self._fallback_merchant_entity_id(
                profile_id=profile_id,
                merchant_key_norm=merchant_key_norm,
            )

        category_id = decision.category_id if decision else None
        if category_id is None:
            category_id = self._resolve_default_category_id(profile_id=profile_id)

        assert category_id is not None

        return {
            "profile_id": profile_id,
            "bank_account_id": bank_account_id,
            "date": parsed_date,
            "montant": amount,
            "devise": str(parsed_row.get("devise") or "CHF"),
            "libelle": parsed_row.get("libelle"),
            "payee": parsed_row.get("payee"),
            "categorie": None,
            "merchant_entity_id": merchant_entity_id,
            "category_id": category_id,
            "meta": meta_dict,
            "merchant_suggestion_created": suggestion_created,
            "contenu_brut": raw_dict,
            "source": source,
        }

    def import_releves(self, request: RelevesImportRequest) -> RelevesImportResult:
        errors: list[RelevesImportError] = []
        normalized_rows: list[dict[str, object]] = []

        merchant_suggestions_created_count = 0

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
                if bool(normalized.get("merchant_suggestion_created")):
                    merchant_suggestions_created_count += 1
                normalized.pop("merchant_suggestion_created", None)
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
            merchant_suggestions_created_count=merchant_suggestions_created_count,
        )
