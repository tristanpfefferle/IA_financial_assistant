"""Releves import orchestrator (analyze/commit)."""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable
from uuid import NAMESPACE_URL, UUID, uuid5

from backend.repositories.profiles_repository import ProfilesRepository
from backend.repositories.releves_repository import RelevesRepository
from backend.repositories.shared_expenses_repository import SupabaseSharedExpensesRepository
from backend.repositories.transaction_clusters_repository import SupabaseTransactionClustersRepository
from backend.services.classification.recurrence import detect_monthly_recurring_clusters
from backend.services.classification.decision_engine import decide_releve_classification, normalize_merchant_alias
from backend.services.releves_import.classification import classify_and_categorize_transaction, resolve_system_category_label
from backend.services.releves_import.dedup import compare_rows
from backend.services.releves_import.routing import route_bank_parser
from backend.services.shared_expenses.auto_share import apply_auto_share_suggestions_for_period
from shared import config
from shared.text_utils import normalize_category_name
from backend.db.supabase_client import SupabaseClient, SupabaseSettings
from shared.models import (
    RelevesImportError,
    RelevesImportMode,
    RelevesImportModifiedAction,
    RelevesImportPreviewItem,
    RelevesImportRequest,
    RelevesImportResult,
)


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RelevesImportService:
    releves_repository: RelevesRepository
    profiles_repository: ProfilesRepository | None = None
    transaction_clusters_repository: SupabaseTransactionClustersRepository | None = None

    _MAX_RECURRING_CLUSTER_SCOPE_ROWS = 20_000

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

    def _resolve_category_id_for_merchant_entity(
        self,
        *,
        profile_id: UUID,
        merchant_entity_id: UUID,
        metadata: dict[str, Any],
    ) -> UUID | None:
        """Resolve a deterministic category for an already linked merchant entity."""

        if self.profiles_repository is None:
            return None

        override = self.profiles_repository.get_profile_merchant_override(
            profile_id=profile_id,
            merchant_entity_id=merchant_entity_id,
        )
        if override and override.get("category_id"):
            return UUID(str(override["category_id"]))

        suggested_norm = self.profiles_repository.get_merchant_entity_suggested_category_norm(
            merchant_entity_id=merchant_entity_id,
        )
        if suggested_norm:
            category_id = self.profiles_repository.find_profile_category_id_by_name_norm(
                profile_id=profile_id,
                name_norm=suggested_norm,
            )
            if category_id is None:
                suggested_label = resolve_system_category_label(suggested_norm)
                if suggested_label:
                    self.profiles_repository.ensure_system_categories(
                        profile_id=profile_id,
                        categories=[{"system_key": suggested_norm, "name": suggested_label}],
                    )
                    category_id = self.profiles_repository.get_profile_category_id_by_system_key(
                        profile_id=profile_id,
                        system_key=suggested_norm,
                    )
            if category_id is not None:
                return category_id

        category_key = str(metadata.get("category_key") or "").strip().lower()
        if category_key and category_key != "other":
            category_label = resolve_system_category_label(category_key)
            if category_label:
                category_name_norm = normalize_category_name(category_label)
                self.profiles_repository.ensure_system_categories(
                    profile_id=profile_id,
                    categories=[{"system_key": category_key, "name": category_label}],
                )
                category_id = self.profiles_repository.find_profile_category_id_by_name_norm(
                    profile_id=profile_id,
                    name_norm=category_name_norm,
                )
                if category_id is None:
                    category_id = self.profiles_repository.get_profile_category_id_by_system_key(
                        profile_id=profile_id,
                        system_key=category_key,
                    )
                if category_id is not None:
                    return category_id

        return self._resolve_default_category_id(profile_id=profile_id)

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

    @staticmethod
    def _build_import_batch_marker(*, profile_id: UUID, imported_at: datetime) -> str:
        """Build one deterministic marker for all rows persisted in one import run."""

        return f"{profile_id}:{imported_at.isoformat()}"

    @staticmethod
    def _to_recurrence_payload(transaction_rows: list[dict[str, object]]) -> list[dict[str, object]]:
        """Map scoped rows to recurrence detector input schema."""

        return [
            {
                "id": str(row.get("id") or ""),
                "date": row.get("date"),
                "montant": row.get("montant"),
                "libelle": row.get("libelle"),
                "payee": row.get("payee"),
            }
            for row in transaction_rows
            if row.get("id") is not None
        ]

    def _resolve_transaction_clusters_repository(self) -> SupabaseTransactionClustersRepository | None:
        if self.transaction_clusters_repository is not None:
            return self.transaction_clusters_repository

        supabase_url = config.supabase_url()
        supabase_key = config.supabase_service_role_key()
        if not supabase_url or not supabase_key:
            return None

        return SupabaseTransactionClustersRepository(
            client=SupabaseClient(
                settings=SupabaseSettings(
                    url=supabase_url,
                    service_role_key=supabase_key,
                    anon_key=config.supabase_anon_key(),
                )
            )
        )

    def _detect_and_persist_recurring_clusters(
        self,
        *,
        profile_id: UUID,
        import_batch_marker: str,
        imported_date_min: date | None,
        imported_date_max: date | None,
    ) -> int:
        if imported_date_min is None or imported_date_max is None:
            return 0

        repository = self._resolve_transaction_clusters_repository()
        if repository is None:
            return 0

        scoped_rows = self.releves_repository.list_releves_for_cluster_detection(
            profile_id=profile_id,
            import_batch_marker=import_batch_marker,
            start_date=imported_date_min,
            end_date=imported_date_max,
            limit=self._MAX_RECURRING_CLUSTER_SCOPE_ROWS,
        )
        if not scoped_rows:
            return 0

        recurrence_input = self._to_recurrence_payload(scoped_rows)
        clusters = detect_monthly_recurring_clusters(recurrence_input)
        for cluster in clusters:
            repository.upsert_cluster(
                profile_id=str(profile_id),
                cluster_type="recurring",
                cluster_key=cluster.cluster_key,
                stats=cluster.stats,
                transaction_ids=cluster.transaction_ids,
            )
        return len(clusters)

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

        merchant_entity_id: UUID | None
        merchant_resolution = "fallback"
        suggestion_created = False

        if self.profiles_repository is None:
            merchant_entity_id = self._fallback_merchant_entity_id(
                profile_id=profile_id,
                merchant_key_norm=merchant_key_norm,
            )
            merchant_resolution = "fallback_no_profiles_repository"
        elif not observed_alias_norm:
            merchant_entity_id = None
            merchant_resolution = "unresolved_empty_alias"
        else:
            alias_candidates = [observed_alias_norm]
            if observed_alias_key_norm and observed_alias_key_norm != observed_alias_norm:
                alias_candidates.append(observed_alias_key_norm)

            merchant_entity_id = None
            for alias_candidate in alias_candidates:
                resolved_entity = self.profiles_repository.find_merchant_entity_by_alias_norm(
                    alias_norm=alias_candidate,
                )
                if resolved_entity and resolved_entity.get("id"):
                    merchant_entity_id = UUID(str(resolved_entity["id"]))
                    merchant_resolution = (
                        "resolved_deterministic"
                        if alias_candidate == observed_alias_norm
                        else "resolved_deterministic_key_norm"
                    )
                    break

            if merchant_entity_id is None:
                merchant_resolution = "unresolved"
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

        meta_dict["merchant_resolution"] = merchant_resolution
        if observed_alias_norm:
            meta_dict["observed_alias_norm"] = observed_alias_norm

        resolved_category_id: UUID | None = None
        if merchant_entity_id is not None:
            resolved_category_id = self._resolve_category_id_for_merchant_entity(
                profile_id=profile_id,
                merchant_entity_id=merchant_entity_id,
                metadata=meta_dict,
            )

        decision = None
        if merchant_entity_id is not None and self.profiles_repository is not None:
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

        category_id = resolved_category_id or (decision.category_id if decision else None)
        if category_id is None and self.profiles_repository is not None:
            category_label = resolve_system_category_label(classification.category_key)
            if category_label:
                category_name_norm = normalize_category_name(category_label)
                if category_name_norm:
                    category_id = self.profiles_repository.find_profile_category_id_by_name_norm(
                        profile_id=profile_id,
                        name_norm=category_name_norm,
                    )
                    if category_id is not None:
                        meta_dict["classification_source"] = "category_key_fallback"
                        meta_dict["classification_rationale"] = "category_key heuristique import"

        if merchant_resolution.startswith("resolved") and merchant_entity_id is None:
            logger.warning(
                "releves_import_resolved_without_merchant_entity_id profile_id=%s bank_account_id=%s external_id=%s observed_alias_norm=%s",
                profile_id,
                bank_account_id,
                external_id,
                observed_alias_norm,
            )

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

    def backfill_categories_for_known_merchant_entities(self, *, profile_id: UUID) -> int:
        """Backfill releves still categorized as `Autres` while already linked to a merchant entity."""

        if self.profiles_repository is None:
            return 0

        autres_category_id = self._resolve_default_category_id(profile_id=profile_id)
        existing_rows = self.releves_repository.list_releves_for_import(
            profile_id=profile_id,
            bank_account_id=None,
        )

        updated = 0
        for row in existing_rows:
            releve_id = row.get("id")
            merchant_entity_id = row.get("merchant_entity_id")
            category_id = row.get("category_id")
            if releve_id is None or merchant_entity_id is None:
                continue
            if category_id != autres_category_id:
                continue

            metadata = row.get("meta") if isinstance(row.get("meta"), dict) else {}
            resolved_category_id = self._resolve_category_id_for_merchant_entity(
                profile_id=profile_id,
                merchant_entity_id=merchant_entity_id,
                metadata=metadata,
            )
            if resolved_category_id is None or resolved_category_id == autres_category_id:
                continue

            self.profiles_repository.attach_merchant_entity_to_releve(
                releve_id=releve_id,
                merchant_entity_id=merchant_entity_id,
                category_id=resolved_category_id,
            )
            updated += 1

        return updated

    def import_releves(
        self,
        request: RelevesImportRequest,
        *,
        on_progress: Callable[[str, int, int], None] | None = None,
    ) -> RelevesImportResult:
        errors: list[RelevesImportError] = []
        normalized_rows: list[dict[str, object]] = []

        merchant_suggestions_created_count = 0

        parsed_batches: list[tuple[str, str, list[dict[str, object]]]] = []
        total_rows_to_categorize = 0

        for file in request.files:
            try:
                content = base64.b64decode(file.content_base64)
                source, parsed_rows = route_bank_parser(file.filename, content)
                parsed_batches.append((file.filename, source, parsed_rows))
                total_rows_to_categorize += len(parsed_rows)
            except Exception as exc:
                errors.append(RelevesImportError(file=file.filename, message=str(exc)))
                continue

        if on_progress:
            on_progress("parsed_total", total_rows_to_categorize, total_rows_to_categorize)

        if on_progress and total_rows_to_categorize > 0:
            on_progress("categorization", 0, total_rows_to_categorize)

        categorized_rows_count = 0
        for file_name, source, parsed_rows in parsed_batches:
            for index, parsed_row in enumerate(parsed_rows):
                try:
                    normalized = self._normalize_row(
                        profile_id=request.profile_id,
                        bank_account_id=request.bank_account_id,
                        parsed_row=parsed_row,
                        source=source,
                    )
                except Exception as exc:
                    debug_detail = ""
                    if (config.get_env("DEBUG_ENDPOINTS_ENABLED", "") or "").strip().lower() in {"1", "true"}:
                        debug_detail = " [debug branch=normalize_row step=row_normalization_failed]"
                    errors.append(
                        RelevesImportError(
                            file=file_name,
                            row_index=index,
                            message=f"{exc}{debug_detail}",
                        )
                    )
                    continue

                if normalized is None:
                    errors.append(
                        RelevesImportError(
                            file=file_name,
                            row_index=index,
                            message="Ligne incomplète (date/montant).",
                        )
                    )
                    continue
                if bool(normalized.get("merchant_suggestion_created")):
                    merchant_suggestions_created_count += 1
                normalized.pop("merchant_suggestion_created", None)
                normalized_rows.append(normalized)
                categorized_rows_count += 1
                if on_progress and total_rows_to_categorize > 0:
                    on_progress("categorization", categorized_rows_count, total_rows_to_categorize)

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
            import_batch_marker = self._build_import_batch_marker(
                profile_id=request.profile_id,
                imported_at=datetime.now(timezone.utc),
            )
            for row in rows_to_insert:
                raw_meta = row.get("meta")
                next_meta = dict(raw_meta) if isinstance(raw_meta, dict) else {}
                next_meta["import_batch_marker"] = import_batch_marker
                row["meta"] = next_meta

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

            imported_dates = [
                row["date"]
                for row in rows_to_insert
                if isinstance(row.get("date"), date)
            ]

            recurring_clusters_detected = 0
            if imported_count > 0 and imported_dates:
                try:
                    recurring_clusters_detected = self._detect_and_persist_recurring_clusters(
                        profile_id=request.profile_id,
                        import_batch_marker=import_batch_marker,
                        imported_date_min=min(imported_dates),
                        imported_date_max=max(imported_dates),
                    )
                except Exception:
                    logger.exception(
                        "releves_import_recurring_clusters_failed profile_id=%s marker=%s",
                        request.profile_id,
                        import_batch_marker,
                    )

            logger.info(
                "releves_import_recurring_clusters_detected profile_id=%s marker=%s clusters=%s",
                request.profile_id,
                import_batch_marker,
                recurring_clusters_detected,
            )

            if rows_to_insert and self.profiles_repository is not None:
                try:
                    if imported_dates:
                        supabase_url = config.supabase_url()
                        supabase_key = config.supabase_service_role_key()
                        if supabase_url and supabase_key:
                            shared_repository = SupabaseSharedExpensesRepository(
                                client=SupabaseClient(
                                    settings=SupabaseSettings(
                                        url=supabase_url,
                                        service_role_key=supabase_key,
                                        anon_key=config.supabase_anon_key(),
                                    )
                                )
                            )
                            apply_auto_share_suggestions_for_period(
                                profile_id=request.profile_id,
                                start_date=min(imported_dates),
                                end_date=max(imported_dates),
                                releves_repository=self.releves_repository,
                                profiles_repository=self.profiles_repository,
                                shared_expenses_repository=shared_repository,
                            )
                except Exception:
                    pass
        else:
            imported_count = 0
            recurring_clusters_detected = 0

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

        all_dates: list[date] = []
        for row in normalized_rows:
            row_date = row.get("date")
            if isinstance(row_date, date):
                all_dates.append(row_date)
            elif isinstance(row_date, str):
                try:
                    all_dates.append(date.fromisoformat(row_date))
                except ValueError:
                    continue

        import_start_date = min(all_dates).isoformat() if all_dates else None
        import_end_date = max(all_dates).isoformat() if all_dates else None

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
            recurring_clusters_detected=recurring_clusters_detected,
            import_start_date=import_start_date,
            import_end_date=import_end_date,
        )
