"""Repository adapters for profils lookup."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Protocol
import unicodedata
from uuid import UUID

from backend.db.supabase_client import SupabaseClient
from shared.models import PROFILE_DEFAULT_CORE_FIELDS


class ProfilesRepository(Protocol):
    def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None) -> UUID | None:
        """Return profile UUID for an authenticated user."""

    def get_chat_state(self, *, profile_id: UUID, user_id: UUID) -> dict[str, Any]:
        """Return persisted chat state for a profile."""

    def update_chat_state(self, *, profile_id: UUID, user_id: UUID, chat_state: dict[str, Any]) -> None:
        """Persist chat state for a profile."""

    def get_profile_fields(self, *, profile_id: UUID, fields: list[str] | None = None) -> dict[str, Any]:
        """Return selected profile columns for one profile id."""

    def update_profile_fields(self, *, profile_id: UUID, set_dict: dict[str, Any]) -> dict[str, Any]:
        """Update selected profile columns for one profile id and return updated values."""

    def list_bank_accounts(self, *, profile_id: UUID) -> list[dict[str, Any]]:
        """Return bank accounts linked to a profile."""

    def ensure_bank_accounts(self, *, profile_id: UUID, names: list[str]) -> dict[str, Any]:
        """Create missing bank accounts while preserving uniqueness by lowercase name."""

    def list_profile_categories(self, *, profile_id: UUID) -> list[dict[str, Any]]:
        """Return categories for a profile and personal scope."""

    def ensure_system_categories(self, *, profile_id: UUID, categories: list[dict[str, str]]) -> dict[str, int]:
        """Create system categories for a profile and return creation counters."""

    def list_merchants_without_category(self, *, profile_id: UUID) -> list[dict[str, Any]]:
        """Return merchants without category for a profile and personal scope."""

    def list_merchants(self, *, profile_id: UUID, limit: int = 5000) -> list[dict[str, Any]]:
        """Return merchants for a profile and personal scope."""

    def get_merchant_by_id(self, *, profile_id: UUID, merchant_id: UUID) -> dict[str, Any] | None:
        """Return one merchant for a profile and personal scope."""

    def create_merchant_suggestions(self, *, profile_id: UUID, suggestions: list[dict[str, Any]]) -> int:
        """Create merchant suggestions and return inserted count."""

    def list_merchant_suggestions(
        self,
        *,
        profile_id: UUID,
        status: str = "pending",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List merchant suggestions for one profile."""

    def get_merchant_suggestion_by_id(self, *, profile_id: UUID, suggestion_id: UUID) -> dict[str, Any] | None:
        """Return one merchant suggestion for one profile."""

    def update_merchant_suggestion_status(
        self,
        *,
        profile_id: UUID,
        suggestion_id: UUID,
        status: str,
        error: str | None = None,
    ) -> None:
        """Update one suggestion status and optional error."""

    def update_merchant_category(self, *, merchant_id: UUID, category_name: str) -> None:
        """Assign a category name on one merchant."""

    def list_releves_without_merchant(self, *, profile_id: UUID, limit: int = 500) -> list[dict[str, Any]]:
        """Return statement rows missing merchant linkage for one profile."""

    def find_merchant_entity_by_alias_norm(self, *, alias_norm: str) -> dict[str, Any] | None:
        """Return one global merchant entity for an alias_norm when available."""

    def upsert_merchant_alias(
        self,
        *,
        merchant_entity_id: UUID,
        alias: str,
        alias_norm: str,
        source: str = "import",
    ) -> None:
        """Create or update one global merchant alias usage counters."""

    def get_profile_merchant_override(
        self,
        *,
        profile_id: UUID,
        merchant_entity_id: UUID,
    ) -> dict[str, Any] | None:
        """Return one profile merchant override for a merchant entity."""

    def upsert_profile_merchant_override(
        self,
        *,
        profile_id: UUID,
        merchant_entity_id: UUID,
        category_id: UUID | None,
        status: str = "auto",
    ) -> None:
        """Create or update one profile merchant override idempotently."""

    def create_map_alias_suggestions(self, *, profile_id: UUID, rows: list[dict[str, Any]]) -> int:
        """Create deduplicated map_alias merchant suggestions."""

    def list_map_alias_suggestions(self, *, profile_id: UUID, limit: int = 100) -> list[dict[str, Any]]:
        """List pending/failed map_alias suggestions for one profile."""

    def count_map_alias_suggestions(self, *, profile_id: UUID) -> int | None:
        """Count pending/failed map_alias suggestions for one profile when supported."""

    def get_merchant_entity_by_canonical_name_norm(
        self,
        *,
        country: str,
        canonical_name_norm: str,
    ) -> dict[str, Any] | None:
        """Return one global merchant entity by canonical_name_norm/country."""

    def create_merchant_entity(
        self,
        *,
        canonical_name: str,
        canonical_name_norm: str,
        country: str,
        suggested_category_norm: str | None,
        suggested_category_label: str | None,
        suggested_confidence: float | None,
        suggested_source: str | None,
    ) -> dict[str, Any]:
        """Create or upsert a global merchant entity and return it."""

    def update_merchant_suggestion_after_resolve(
        self,
        *,
        profile_id: UUID,
        suggestion_id: UUID,
        status: str,
        error: str | None,
        llm_model: str | None,
        llm_run_id: str | None,
        confidence: float | None,
        rationale: str | None,
        target_merchant_entity_id: UUID | None,
        suggested_entity_name: str | None,
        suggested_entity_name_norm: str | None,
        suggested_category_norm: str | None,
        suggested_category_label: str | None,
    ) -> None:
        """Update one map_alias suggestion outcome after LLM resolution."""

    def apply_entity_to_profile_transactions(
        self,
        *,
        profile_id: UUID,
        observed_alias: str,
        merchant_entity_id: UUID,
        category_id: UUID | None,
    ) -> int:
        """Apply resolved merchant entity to best-effort matching profile transactions."""

    def attach_merchant_entity_to_releve(
        self,
        *,
        releve_id: UUID,
        merchant_entity_id: UUID,
        category_id: UUID | None,
    ) -> None:
        """Attach one global merchant entity and optional category to one bank statement row."""

    def upsert_merchant_by_name_norm(
        self,
        *,
        profile_id: UUID,
        name: str,
        name_norm: str,
        scope: str = "personal",
    ) -> UUID:
        """Find or create merchant id for a profile/name_norm pair."""

    def attach_merchant_to_releve(self, *, releve_id: UUID, merchant_id: UUID) -> None:
        """Attach one merchant id to one bank statement row."""

    def append_merchant_alias(self, *, merchant_id: UUID, alias: str) -> None:
        """Append one observed raw alias to a merchant when missing."""

    def rename_merchant(self, *, profile_id: UUID, merchant_id: UUID, new_name: str) -> dict[str, str]:
        """Rename one merchant for a profile while preserving aliases."""

    def merge_merchants(
        self,
        *,
        profile_id: UUID,
        source_merchant_id: UUID,
        target_merchant_id: UUID,
    ) -> dict[str, Any]:
        """Merge source merchant into target merchant for one profile."""

    def hard_reset_profile(self, *, profile_id: UUID, user_id: UUID) -> None:
        """Purge profile-scoped data and reset onboarding/profile fields."""


class SupabaseProfilesRepository:
    """Supabase repository for profils table lookups."""

    def __init__(self, client: SupabaseClient) -> None:
        self._client = client

    def _get_profile_id_by_column(self, *, column: str, value: str) -> UUID | None:
        rows, _ = self._client.get_rows(
            table="profils",
            query={"select": "id", column: f"eq.{value}", "limit": 1},
            with_count=False,
            use_anon_key=False,
        )
        if not rows:
            return None
        profile_id = rows[0].get("id")
        if not profile_id:
            return None
        return UUID(str(profile_id))

    def get_profile_id_for_auth_user(self, *, auth_user_id: UUID, email: str | None) -> UUID | None:
        profile_id = self._get_profile_id_by_column(column="account_id", value=str(auth_user_id))
        if profile_id is not None:
            return profile_id

        if email:
            return self._get_profile_id_by_column(column="email", value=email)
        return None

    def get_chat_state(self, *, profile_id: UUID, user_id: UUID) -> dict[str, Any]:
        conversation_id = str(profile_id)
        rows, _ = self._client.get_rows(
            table="chat_state",
            query={
                "select": "active_task,state",
                "conversation_id": f"eq.{conversation_id}",
                "profile_id": f"eq.{profile_id}",
                "user_id": f"eq.{user_id}",
                "limit": 1,
            },
            with_count=False,
            use_anon_key=False,
        )
        if not rows:
            return {}
        row = rows[0] or {}
        result: dict[str, Any] = {}
        active_task = row.get("active_task")
        state = row.get("state")
        if active_task is not None:
            result["active_task"] = active_task
        if state is not None:
            result["state"] = state
        return result

    def update_chat_state(self, *, profile_id: UUID, user_id: UUID, chat_state: dict[str, Any]) -> None:
        conversation_id = str(profile_id)
        payload = {
            "conversation_id": conversation_id,
            "user_id": str(user_id),
            "profile_id": str(profile_id),
        }

        if "active_task" in chat_state:
            payload["active_task"] = chat_state.get("active_task")
        if "state" in chat_state:
            payload["state"] = chat_state.get("state")

        self._client.upsert_row(
            table="chat_state",
            payload=payload,
            on_conflict="conversation_id",
            use_anon_key=False,
        )

    @staticmethod
    def _serialize_profile_value(value: Any) -> Any:
        if isinstance(value, (date, UUID)):
            return str(value)
        return value

    @staticmethod
    def _filter_allowed_profile_updates(set_dict: dict[str, Any]) -> dict[str, Any]:
        allowed_fields = {"first_name", "last_name", "birth_date"}
        return {
            field: SupabaseProfilesRepository._serialize_profile_value(value)
            for field, value in set_dict.items()
            if field in allowed_fields
        }

    def get_profile_fields(self, *, profile_id: UUID, fields: list[str] | None = None) -> dict[str, Any]:
        selected_fields = list(fields or PROFILE_DEFAULT_CORE_FIELDS)
        select_clause = ",".join(dict.fromkeys(selected_fields))
        rows, _ = self._client.get_rows(
            table="profils",
            query={"select": select_clause, "id": f"eq.{profile_id}", "limit": 1},
            with_count=False,
            use_anon_key=False,
        )
        if not rows:
            raise ValueError("Profile not found")

        row = rows[0]
        return {field: row.get(field) for field in selected_fields}

    def update_profile_fields(self, *, profile_id: UUID, set_dict: dict[str, Any]) -> dict[str, Any]:
        filtered_set_dict = self._filter_allowed_profile_updates(set_dict)
        if not filtered_set_dict:
            return {}

        if hasattr(self._client, "table"):
            response = (
                self._client.table("profils")
                .update(filtered_set_dict)
                .eq("id", str(profile_id))
                .execute()
            )
            response_data = getattr(response, "data", None)
            if response_data == []:
                raise ValueError("Profile not found")
            return dict(filtered_set_dict)

        rows = self._client.patch_rows(
            table="profils",
            query={"id": f"eq.{profile_id}"},
            payload=filtered_set_dict,
            use_anon_key=False,
        )
        if not rows:
            raise ValueError("Profile not found")

        row = rows[0]
        return {field: row.get(field) for field in filtered_set_dict}


    @staticmethod
    def _normalize_bank_account_names(names: list[str]) -> list[str]:
        normalized_names: list[str] = []
        seen_lower: set[str] = set()
        for raw_name in names:
            cleaned_name = " ".join(str(raw_name).strip().split())
            if not cleaned_name:
                continue
            lowered_name = cleaned_name.lower()
            if lowered_name in seen_lower:
                continue
            seen_lower.add(lowered_name)
            normalized_names.append(cleaned_name)
        return normalized_names

    def list_bank_accounts(self, *, profile_id: UUID) -> list[dict[str, Any]]:
        rows, _ = self._client.get_rows(
            table="bank_accounts",
            query={
                "select": "id,name,account_kind,kind",
                "profile_id": f"eq.{profile_id}",
                "limit": 200,
            },
            with_count=False,
            use_anon_key=False,
        )
        return rows

    def ensure_bank_accounts(self, *, profile_id: UUID, names: list[str]) -> dict[str, Any]:
        normalized_names = self._normalize_bank_account_names(names)
        existing_rows = self.list_bank_accounts(profile_id=profile_id)
        existing_by_lower = {str(row.get("name", "")).strip().lower(): row for row in existing_rows if row.get("name")}

        created: list[str] = []
        existing: list[str] = []

        for name in normalized_names:
            lowered_name = name.lower()
            if lowered_name in existing_by_lower:
                existing.append(name)
                continue

            payload = {
                "profile_id": str(profile_id),
                "name": name,
                "kind": "individual",
                "account_kind": "personal_current",
                "is_system": False,
            }
            try:
                self._client.post_rows(table="bank_accounts", payload=payload, use_anon_key=False)
            except RuntimeError as exc:
                error_message = str(exc).lower()
                if "duplicate key" in error_message or "unique" in error_message:
                    existing.append(name)
                    continue
                raise

            created.append(name)
            existing_by_lower[lowered_name] = {"name": name}

        return {"created": created, "existing": existing, "all": normalized_names}

    @staticmethod
    def _normalize_name_norm(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value.strip().lower()).encode("ascii", "ignore").decode("ascii")
        return " ".join(normalized.split())

    def list_profile_categories(self, *, profile_id: UUID) -> list[dict[str, Any]]:
        rows, _ = self._client.get_rows(
            table="profile_categories",
            query={
                "select": "id,name,name_norm,system_key,is_system,scope",
                "profile_id": f"eq.{profile_id}",
                "scope": "eq.personal",
                "limit": 200,
            },
            with_count=False,
            use_anon_key=False,
        )
        return rows

    def ensure_system_categories(self, *, profile_id: UUID, categories: list[dict[str, str]]) -> dict[str, int]:
        existing = self.list_profile_categories(profile_id=profile_id)
        existing_system_keys = {
            str(row.get("system_key"))
            for row in existing
            if row.get("system_key")
        }
        existing_name_norms = {
            str(row.get("name_norm"))
            for row in existing
            if row.get("name_norm")
        }

        created_count = 0
        for category in categories:
            system_key = str(category.get("system_key", "")).strip()
            name = str(category.get("name", "")).strip()
            if not system_key or not name:
                continue
            name_norm = self._normalize_name_norm(name)
            if system_key in existing_system_keys or name_norm in existing_name_norms:
                continue

            payload = {
                "profile_id": str(profile_id),
                "scope": "personal",
                "is_system": True,
                "system_key": system_key,
                "name": name,
                "name_norm": name_norm,
                "keywords": [],
            }
            try:
                self._client.post_rows(table="profile_categories", payload=payload, use_anon_key=False)
                created_count += 1
                existing_system_keys.add(system_key)
                existing_name_norms.add(name_norm)
            except RuntimeError as exc:
                error_message = str(exc).lower()
                if "duplicate key" in error_message or "unique" in error_message:
                    continue
                raise

        return {"created_count": created_count, "system_total_count": len(existing_system_keys)}

    def list_merchants_without_category(self, *, profile_id: UUID) -> list[dict[str, Any]]:
        rows, _ = self._client.get_rows(
            table="merchants",
            query={
                "select": "id,name,name_norm,category",
                "profile_id": f"eq.{profile_id}",
                "scope": "eq.personal",
                "or": "(category.is.null,category.eq.)",
                "limit": 500,
            },
            with_count=False,
            use_anon_key=False,
        )
        return rows

    def list_merchants(self, *, profile_id: UUID, limit: int = 5000) -> list[dict[str, Any]]:
        rows, _ = self._client.get_rows(
            table="merchants",
            query={
                "select": "id,name,name_norm,aliases,category",
                "profile_id": f"eq.{profile_id}",
                "scope": "eq.personal",
                "limit": max(1, limit),
            },
            with_count=False,
            use_anon_key=False,
        )
        return rows

    def get_merchant_by_id(self, *, profile_id: UUID, merchant_id: UUID) -> dict[str, Any] | None:
        rows, _ = self._client.get_rows(
            table="merchants",
            query={
                "select": "id,name,name_norm,aliases,category",
                "profile_id": f"eq.{profile_id}",
                "scope": "eq.personal",
                "id": f"eq.{merchant_id}",
                "limit": 1,
            },
            with_count=False,
            use_anon_key=False,
        )
        if not rows:
            return None
        return rows[0]

    def create_merchant_suggestions(self, *, profile_id: UUID, suggestions: list[dict[str, Any]]) -> int:
        if not suggestions:
            return 0

        rows_payload = [{**suggestion, "profile_id": str(profile_id)} for suggestion in suggestions]
        inserted_rows = self._client.post_rows(
            table="merchant_suggestions",
            payload=rows_payload,
            use_anon_key=False,
        )
        return len(inserted_rows)

    def list_merchant_suggestions(
        self,
        *,
        profile_id: UUID,
        status: str = "pending",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows, _ = self._client.get_rows(
            table="merchant_suggestions",
            query={
                "select": "id,profile_id,created_at,status,action,source_merchant_id,target_merchant_id,suggested_name,suggested_name_norm,suggested_category,confidence,rationale,error,sample_aliases,llm_model,llm_run_id",
                "profile_id": f"eq.{profile_id}",
                "status": f"eq.{status}",
                "order": "created_at.desc",
                "limit": max(1, limit),
            },
            with_count=False,
            use_anon_key=False,
        )
        return rows

    def get_merchant_suggestion_by_id(self, *, profile_id: UUID, suggestion_id: UUID) -> dict[str, Any] | None:
        rows, _ = self._client.get_rows(
            table="merchant_suggestions",
            query={
                "select": "id,profile_id,created_at,status,action,source_merchant_id,target_merchant_id,suggested_name,suggested_name_norm,suggested_category,confidence,rationale,error,sample_aliases,llm_model,llm_run_id",
                "profile_id": f"eq.{profile_id}",
                "id": f"eq.{suggestion_id}",
                "limit": 1,
            },
            with_count=False,
            use_anon_key=False,
        )
        if not rows:
            return None
        return rows[0]

    def update_merchant_suggestion_status(
        self,
        *,
        profile_id: UUID,
        suggestion_id: UUID,
        status: str,
        error: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"status": status, "error": error}
        self._client.patch_rows(
            table="merchant_suggestions",
            query={"profile_id": f"eq.{profile_id}", "id": f"eq.{suggestion_id}"},
            payload=payload,
            use_anon_key=False,
        )

    def hard_reset_profile(self, *, profile_id: UUID, user_id: UUID) -> None:
        self._client.patch_rows(
            table="profils",
            query={"id": f"eq.{profile_id}"},
            payload={"first_name": None, "last_name": None, "birth_date": None},
            use_anon_key=False,
        )

        self.update_chat_state(profile_id=profile_id, user_id=user_id, chat_state={})

        for table_name in ("releves_bancaires", "merchants", "profile_categories", "bank_accounts"):
            self._client.delete_rows(
                table=table_name,
                query={"profile_id": f"eq.{profile_id}"},
                use_anon_key=False,
            )

    def update_merchant_category(self, *, merchant_id: UUID, category_name: str) -> None:
        cleaned = " ".join(category_name.strip().split())
        if not cleaned:
            return
        self._client.patch_rows(
            table="merchants",
            query={"id": f"eq.{merchant_id}"},
            payload={"category": cleaned},
            use_anon_key=False,
        )

    def list_releves_without_merchant(self, *, profile_id: UUID, limit: int = 500) -> list[dict[str, Any]]:
        # PostgREST filtering can reliably exclude NULL but not all empty-string variants.
        # We keep this broad query and let the merchant bootstrap flow apply final Python-side
        # filtering on stripped `payee`/`libelle` values before creating/linking merchants.
        rows, _ = self._client.get_rows(
            table="releves_bancaires",
            query={
                "select": "id,payee,libelle,created_at,date",
                "profile_id": f"eq.{profile_id}",
                "merchant_entity_id": "is.null",
                "or": "(payee.not.is.null,libelle.not.is.null)",
                "limit": max(1, limit),
            },
            with_count=False,
            use_anon_key=False,
        )
        return rows

    def find_merchant_entity_by_alias_norm(self, *, alias_norm: str) -> dict[str, Any] | None:
        cleaned_alias_norm = self._normalize_name_norm(alias_norm)
        if not cleaned_alias_norm:
            return None

        rows, _ = self._client.get_rows(
            table="merchant_aliases",
            query={
                "select": "merchant_entity_id,alias,alias_norm,merchant_entities(id,canonical_name,canonical_name_norm,suggested_category_norm,suggested_category_label,suggested_confidence)",
                "alias_norm": f"eq.{cleaned_alias_norm}",
                "limit": 1,
            },
            with_count=False,
            use_anon_key=False,
        )
        if not rows:
            return None

        row = rows[0]
        entity = row.get("merchant_entities") if isinstance(row.get("merchant_entities"), dict) else None
        if entity is None:
            return None
        entity_id = entity.get("id") or row.get("merchant_entity_id")
        if not entity_id:
            return None

        return {
            "id": str(entity_id),
            "canonical_name": entity.get("canonical_name"),
            "canonical_name_norm": entity.get("canonical_name_norm"),
            "suggested_category_norm": entity.get("suggested_category_norm"),
            "suggested_category_label": entity.get("suggested_category_label"),
            "suggested_confidence": entity.get("suggested_confidence"),
        }

    def upsert_merchant_alias(
        self,
        *,
        merchant_entity_id: UUID,
        alias: str,
        alias_norm: str,
        source: str = "import",
    ) -> None:
        cleaned_alias = " ".join(alias.strip().split())
        cleaned_alias_norm = self._normalize_name_norm(alias_norm)
        if not cleaned_alias or not cleaned_alias_norm:
            return

        rows, _ = self._client.get_rows(
            table="merchant_aliases",
            query={
                "select": "id,times_seen",
                "merchant_entity_id": f"eq.{merchant_entity_id}",
                "alias_norm": f"eq.{cleaned_alias_norm}",
                "limit": 1,
            },
            with_count=False,
            use_anon_key=False,
        )
        now_iso = datetime.now(timezone.utc).isoformat()
        if rows and rows[0].get("id"):
            row = rows[0]
            times_seen = int(row.get("times_seen") or 0) + 1
            self._client.patch_rows(
                table="merchant_aliases",
                query={"id": f"eq.{row['id']}"},
                payload={"times_seen": times_seen, "last_seen": now_iso},
                use_anon_key=False,
            )
            return

        self._client.post_rows(
            table="merchant_aliases",
            payload={
                "merchant_entity_id": str(merchant_entity_id),
                "alias": cleaned_alias,
                "alias_norm": cleaned_alias_norm,
                "times_seen": 1,
                "last_seen": now_iso,
                "source": source,
            },
            use_anon_key=False,
        )

    def get_profile_merchant_override(
        self,
        *,
        profile_id: UUID,
        merchant_entity_id: UUID,
    ) -> dict[str, Any] | None:
        rows, _ = self._client.get_rows(
            table="profile_merchant_overrides",
            query={
                "select": "id,profile_id,merchant_entity_id,display_name_override,category_id,status",
                "profile_id": f"eq.{profile_id}",
                "merchant_entity_id": f"eq.{merchant_entity_id}",
                "limit": 1,
            },
            with_count=False,
            use_anon_key=False,
        )
        if not rows:
            return None
        return rows[0]

    def upsert_profile_merchant_override(
        self,
        *,
        profile_id: UUID,
        merchant_entity_id: UUID,
        category_id: UUID | None,
        status: str = "auto",
    ) -> None:
        payload = {
            "profile_id": str(profile_id),
            "merchant_entity_id": str(merchant_entity_id),
            "category_id": str(category_id) if category_id else None,
            "status": status,
        }
        self._client.upsert_row(
            table="profile_merchant_overrides",
            payload=payload,
            on_conflict="profile_id,merchant_entity_id",
            use_anon_key=False,
        )

    def create_map_alias_suggestions(self, *, profile_id: UUID, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0

        inserted_count = 0
        seen_in_batch: set[str] = set()
        for row in rows:
            observed_alias_norm = self._normalize_name_norm(str(row.get("observed_alias_norm") or ""))
            if not observed_alias_norm or observed_alias_norm in seen_in_batch:
                continue
            seen_in_batch.add(observed_alias_norm)

            existing_rows, _ = self._client.get_rows(
                table="merchant_suggestions",
                query={
                    "select": "id",
                    "profile_id": f"eq.{profile_id}",
                    "action": "eq.map_alias",
                    "observed_alias_norm": f"eq.{observed_alias_norm}",
                    "status": "in.(pending,failed)",
                    "limit": 1,
                },
                with_count=False,
                use_anon_key=False,
            )
            if existing_rows:
                continue

            payload = {
                "profile_id": str(profile_id),
                "action": "map_alias",
                "status": row.get("status") or "pending",
                "observed_alias": row.get("observed_alias"),
                "observed_alias_norm": observed_alias_norm,
                "suggested_entity_name": row.get("suggested_entity_name"),
                "confidence": row.get("confidence"),
                "rationale": row.get("rationale"),
            }
            self._client.post_rows(table="merchant_suggestions", payload=payload, use_anon_key=False)
            inserted_count += 1

        return inserted_count

    def list_map_alias_suggestions(self, *, profile_id: UUID, limit: int = 100) -> list[dict[str, Any]]:
        rows, _ = self._client.get_rows(
            table="merchant_suggestions",
            query={
                "select": "id,observed_alias,observed_alias_norm,created_at",
                "profile_id": f"eq.{profile_id}",
                "action": "eq.map_alias",
                "status": "in.(pending,failed)",
                "order": "created_at.asc",
                "limit": max(1, limit),
            },
            with_count=False,
            use_anon_key=False,
        )
        return rows

    def count_map_alias_suggestions(self, *, profile_id: UUID) -> int | None:
        _, total = self._client.get_rows(
            table="merchant_suggestions",
            query={
                "select": "id",
                "profile_id": f"eq.{profile_id}",
                "action": "eq.map_alias",
                "status": "in.(pending,failed)",
                "limit": 1,
            },
            with_count=True,
            use_anon_key=False,
        )
        return total

    def get_merchant_entity_by_canonical_name_norm(
        self,
        *,
        country: str,
        canonical_name_norm: str,
    ) -> dict[str, Any] | None:
        cleaned_country = country.strip().upper() or "CH"
        cleaned_name_norm = self._normalize_name_norm(canonical_name_norm)
        if not cleaned_name_norm:
            return None

        rows, _ = self._client.get_rows(
            table="merchant_entities",
            query={
                "select": "id,canonical_name,canonical_name_norm,country,suggested_category_norm,suggested_category_label,suggested_confidence,suggested_source",
                "country": f"eq.{cleaned_country}",
                "canonical_name_norm": f"eq.{cleaned_name_norm}",
                "limit": 1,
            },
            with_count=False,
            use_anon_key=False,
        )
        return rows[0] if rows else None

    def create_merchant_entity(
        self,
        *,
        canonical_name: str,
        canonical_name_norm: str,
        country: str,
        suggested_category_norm: str | None,
        suggested_category_label: str | None,
        suggested_confidence: float | None,
        suggested_source: str | None,
    ) -> dict[str, Any]:
        cleaned_name = " ".join(canonical_name.strip().split())
        cleaned_name_norm = self._normalize_name_norm(canonical_name_norm)
        cleaned_country = country.strip().upper() or "CH"
        if not cleaned_name or not cleaned_name_norm:
            raise ValueError("canonical_name and canonical_name_norm must be non-empty")

        existing = self.get_merchant_entity_by_canonical_name_norm(
            country=cleaned_country,
            canonical_name_norm=cleaned_name_norm,
        )
        if existing is not None:
            return existing

        payload = {
            "canonical_name": cleaned_name,
            "canonical_name_norm": cleaned_name_norm,
            "country": cleaned_country,
            "suggested_category_norm": suggested_category_norm,
            "suggested_category_label": suggested_category_label,
            "suggested_confidence": suggested_confidence,
            "suggested_source": suggested_source,
        }

        try:
            created_rows = self._client.post_rows(
                table="merchant_entities",
                payload=payload,
                use_anon_key=False,
            )
            if created_rows:
                return created_rows[0]
        except RuntimeError as exc:
            error_message = str(exc).lower()
            if "duplicate key" not in error_message and "unique" not in error_message:
                raise

        existing = self.get_merchant_entity_by_canonical_name_norm(
            country=cleaned_country,
            canonical_name_norm=cleaned_name_norm,
        )
        if existing is not None:
            return existing
        raise RuntimeError("unable to upsert merchant entity")

    def update_merchant_suggestion_after_resolve(
        self,
        *,
        profile_id: UUID,
        suggestion_id: UUID,
        status: str,
        error: str | None,
        llm_model: str | None,
        llm_run_id: str | None,
        confidence: float | None,
        rationale: str | None,
        target_merchant_entity_id: UUID | None,
        suggested_entity_name: str | None,
        suggested_entity_name_norm: str | None,
        suggested_category_norm: str | None,
        suggested_category_label: str | None,
    ) -> None:
        payload: dict[str, Any] = {
            "status": status,
            "error": error,
            "llm_model": llm_model,
            "llm_run_id": llm_run_id,
            "confidence": confidence,
            "rationale": rationale,
            "target_merchant_entity_id": str(target_merchant_entity_id) if target_merchant_entity_id else None,
            "suggested_entity_name": suggested_entity_name,
            "suggested_entity_name_norm": self._normalize_name_norm(suggested_entity_name_norm or "")
            if suggested_entity_name_norm
            else None,
            "suggested_category_norm": self._normalize_name_norm(suggested_category_norm or "")
            if suggested_category_norm
            else None,
            "suggested_category_label": suggested_category_label,
        }
        self._client.patch_rows(
            table="merchant_suggestions",
            query={"profile_id": f"eq.{profile_id}", "id": f"eq.{suggestion_id}"},
            payload=payload,
            use_anon_key=False,
        )

    def apply_entity_to_profile_transactions(
        self,
        *,
        profile_id: UUID,
        observed_alias: str,
        merchant_entity_id: UUID,
        category_id: UUID | None,
    ) -> int:
        alias_raw = " ".join(observed_alias.strip().split())
        if not alias_raw:
            return 0

        def _pg_quote(value: str) -> str:
            escaped = value.replace('"', '\\"')
            return f'"{escaped}"'

        payload: dict[str, Any] = {"merchant_entity_id": str(merchant_entity_id)}
        if category_id is not None:
            payload["category_id"] = str(category_id)

        def _count_and_patch(or_filter: str) -> int:
            rows, total = self._client.get_rows(
                table="releves_bancaires",
                query={
                    "select": "id",
                    "profile_id": f"eq.{profile_id}",
                    "merchant_entity_id": "is.null",
                    "or": or_filter,
                    "limit": 1,
                },
                with_count=True,
                use_anon_key=False,
            )
            matched_count = int(total or 0)
            if matched_count <= 0 and not rows:
                return 0
            self._client.patch_rows(
                table="releves_bancaires",
                query={
                    "profile_id": f"eq.{profile_id}",
                    "merchant_entity_id": "is.null",
                    "or": or_filter,
                },
                payload=payload,
                use_anon_key=False,
            )
            if matched_count > 0:
                return matched_count
            return len(rows)

        exact_filter = f"(payee.eq.{_pg_quote(alias_raw)},libelle.eq.{_pg_quote(alias_raw)})"
        updated_exact = _count_and_patch(exact_filter)
        if updated_exact > 0:
            return updated_exact

        ilike_value = f"*{alias_raw}*"
        ilike_filter = f"(payee.ilike.{_pg_quote(ilike_value)},libelle.ilike.{_pg_quote(ilike_value)})"
        return _count_and_patch(ilike_filter)

    def attach_merchant_entity_to_releve(
        self,
        *,
        releve_id: UUID,
        merchant_entity_id: UUID,
        category_id: UUID | None,
    ) -> None:
        payload: dict[str, Any] = {"merchant_entity_id": str(merchant_entity_id)}
        if category_id is not None:
            payload["category_id"] = str(category_id)
        self._client.patch_rows(
            table="releves_bancaires",
            query={"id": f"eq.{releve_id}"},
            payload=payload,
            use_anon_key=False,
        )

    def upsert_merchant_by_name_norm(
        self,
        *,
        profile_id: UUID,
        name: str,
        name_norm: str,
        scope: str = "personal",
    ) -> UUID:
        cleaned_name = " ".join(name.strip().split())
        cleaned_name_norm = self._normalize_name_norm(name_norm)
        now_iso = datetime.now(timezone.utc).isoformat()
        if not cleaned_name or not cleaned_name_norm:
            raise ValueError("merchant name and name_norm must be non-empty")

        query = {
            "select": "id",
            "profile_id": f"eq.{profile_id}",
            "scope": f"eq.{scope}",
            "name_norm": f"eq.{cleaned_name_norm}",
            "limit": 1,
        }

        existing_rows, _ = self._client.get_rows(
            table="merchants",
            query=query,
            with_count=False,
            use_anon_key=False,
        )
        if existing_rows and existing_rows[0].get("id"):
            merchant_id = UUID(str(existing_rows[0]["id"]))
            try:
                self._client.patch_rows(
                    table="merchants",
                    query={"id": f"eq.{merchant_id}"},
                    payload={"last_seen": now_iso},
                    use_anon_key=False,
                )
            except Exception:
                pass
            return merchant_id

        payload = {
            "profile_id": str(profile_id),
            "scope": scope,
            "name": cleaned_name,
            "name_norm": cleaned_name_norm,
            "aliases": [],
            "last_seen": now_iso,
        }
        try:
            created_rows = self._client.post_rows(table="merchants", payload=payload, use_anon_key=False)
            if created_rows and created_rows[0].get("id"):
                return UUID(str(created_rows[0]["id"]))
        except RuntimeError as exc:
            if "duplicate key" not in str(exc).lower() and "unique" not in str(exc).lower():
                raise

        fallback_rows, _ = self._client.get_rows(
            table="merchants",
            query=query,
            with_count=False,
            use_anon_key=False,
        )
        if fallback_rows and fallback_rows[0].get("id"):
            return UUID(str(fallback_rows[0]["id"]))
        raise RuntimeError("unable to upsert merchant")

    def attach_merchant_to_releve(self, *, releve_id: UUID, merchant_id: UUID) -> None:
        self._client.patch_rows(
            table="releves_bancaires",
            query={"id": f"eq.{releve_id}"},
            payload={"merchant_id": str(merchant_id)},
            use_anon_key=False,
        )

    def append_merchant_alias(self, *, merchant_id: UUID, alias: str) -> None:
        cleaned_alias = " ".join(alias.split())
        if not cleaned_alias:
            return

        rows, _ = self._client.get_rows(
            table="merchants",
            query={"select": "aliases", "id": f"eq.{merchant_id}", "limit": 1},
            with_count=False,
            use_anon_key=False,
        )
        existing_aliases_raw = rows[0].get("aliases") if rows else []
        existing_aliases = existing_aliases_raw if isinstance(existing_aliases_raw, list) else []
        if cleaned_alias in existing_aliases:
            return

        self._client.patch_rows(
            table="merchants",
            query={"id": f"eq.{merchant_id}"},
            payload={"aliases": [*existing_aliases, cleaned_alias]},
            use_anon_key=False,
        )

    def rename_merchant(self, *, profile_id: UUID, merchant_id: UUID, new_name: str) -> dict[str, str]:
        cleaned_name = " ".join(str(new_name).strip().split())
        if not cleaned_name:
            raise ValueError("merchant name must be non-empty")

        cleaned_name_norm = self._normalize_name_norm(cleaned_name)
        updated_rows = self._client.patch_rows(
            table="merchants",
            query={"id": f"eq.{merchant_id}", "profile_id": f"eq.{profile_id}"},
            payload={"name": cleaned_name, "name_norm": cleaned_name_norm},
            use_anon_key=False,
        )
        if not updated_rows:
            raise ValueError("merchant not found for this profile")

        return {
            "merchant_id": str(merchant_id),
            "name": cleaned_name,
            "name_norm": cleaned_name_norm,
        }

    @staticmethod
    def _normalize_aliases(raw_aliases: Any) -> list[str]:
        if not isinstance(raw_aliases, list):
            return []
        aliases: list[str] = []
        for alias in raw_aliases:
            cleaned_alias = " ".join(str(alias).split())
            if cleaned_alias:
                aliases.append(cleaned_alias)
        return aliases

    def merge_merchants(
        self,
        *,
        profile_id: UUID,
        source_merchant_id: UUID,
        target_merchant_id: UUID,
    ) -> dict[str, Any]:
        if source_merchant_id == target_merchant_id:
            raise ValueError("source_merchant_id and target_merchant_id must be different")

        source_rows, _ = self._client.get_rows(
            table="merchants",
            query={
                "select": "id,profile_id,scope,name,name_norm,aliases,category",
                "id": f"eq.{source_merchant_id}",
                "limit": 1,
            },
            with_count=False,
            use_anon_key=False,
        )
        target_rows, _ = self._client.get_rows(
            table="merchants",
            query={
                "select": "id,profile_id,scope,name,name_norm,aliases,category",
                "id": f"eq.{target_merchant_id}",
                "limit": 1,
            },
            with_count=False,
            use_anon_key=False,
        )

        if not source_rows or not target_rows:
            raise ValueError("source or target merchant not found")

        source = source_rows[0]
        target = target_rows[0]
        for merchant in (source, target):
            merchant_profile_id = str(merchant.get("profile_id") or "")
            if merchant_profile_id != str(profile_id):
                raise ValueError("merchant does not belong to provided profile")
            if str(merchant.get("scope") or "") != "personal":
                raise ValueError("merchant scope must be personal")

        target_aliases = self._normalize_aliases(target.get("aliases"))
        source_aliases = self._normalize_aliases(source.get("aliases"))
        aliases_final = list(target_aliases)
        seen_aliases = set(target_aliases)

        for alias in source_aliases:
            if alias in seen_aliases:
                continue
            seen_aliases.add(alias)
            aliases_final.append(alias)

        source_name = " ".join(str(source.get("name") or "").split())
        if source_name and source_name not in seen_aliases:
            aliases_final.append(source_name)
            seen_aliases.add(source_name)

        releves_rows, _ = self._client.get_rows(
            table="releves_bancaires",
            query={
                "select": "id",
                "profile_id": f"eq.{profile_id}",
                "merchant_id": f"eq.{source_merchant_id}",
                "limit": 5000,
            },
            with_count=False,
            use_anon_key=False,
        )
        releve_ids = [str(row.get("id")) for row in releves_rows if row.get("id")]

        if releve_ids:
            joined_ids = ",".join(releve_ids)
            self._client.patch_rows(
                table="releves_bancaires",
                query={"id": f"in.({joined_ids})"},
                payload={"merchant_id": str(target_merchant_id)},
                use_anon_key=False,
            )

        moved_releves_count = len(releve_ids)

        updated_target_rows = self._client.patch_rows(
            table="merchants",
            query={"id": f"eq.{target_merchant_id}", "profile_id": f"eq.{profile_id}"},
            payload={"aliases": aliases_final},
            use_anon_key=False,
        )
        if not updated_target_rows:
            raise ValueError("target merchant not found for update")

        deleted_rows = self._client.delete_rows(
            table="merchants",
            query={"id": f"eq.{source_merchant_id}", "profile_id": f"eq.{profile_id}"},
            use_anon_key=False,
        )
        if not deleted_rows:
            raise ValueError("source merchant not found for deletion")

        return {
            "target_merchant_id": str(target_merchant_id),
            "source_merchant_id": str(source_merchant_id),
            "moved_releves_count": moved_releves_count,
            "aliases_added_count": max(0, len(aliases_final) - len(target_aliases)),
            "target_aliases_count": len(aliases_final),
        }
