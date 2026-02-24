"""Repository adapters for shared expenses and suggestions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID, uuid4

from backend.db.supabase_client import SupabaseClient


@dataclass(slots=True)
class SharedExpenseRow:
    """Shared expense materialized row used for effective spending reporting."""

    from_profile_id: UUID
    to_profile_id: UUID | None
    transaction_id: UUID | None
    amount: Decimal
    created_at: datetime | None
    status: str
    split_ratio_other: Decimal | None
    other_party_label: str | None = None


@dataclass(slots=True)
class SharedExpenseSuggestionRow:
    """Pending/processed shared expense suggestion row."""

    id: UUID
    profile_id: UUID
    transaction_id: UUID
    suggested_to_profile_id: UUID | None
    suggested_split_ratio_other: Decimal
    status: str
    confidence: float | None
    rationale: str | None
    link_id: UUID | None
    link_pair_id: UUID | None
    other_party_label: str | None = None


class SharedExpensesRepository(Protocol):
    def list_auto_share_categories(self, *, profile_id: UUID) -> list[dict[str, Any]]:
        """Return categories configured for deterministic auto-share."""

    def create_shared_expense_suggestions_bulk(self, *, profile_id: UUID, suggestions: list[dict[str, Any]]) -> int:
        """Insert suggestion rows while tolerating pending dedup collisions."""

    def list_shared_expense_suggestions(
        self,
        *,
        profile_id: UUID,
        status: str = "pending",
        limit: int = 100,
    ) -> list[SharedExpenseSuggestionRow]:
        """List shared expense suggestions for one profile."""

    def mark_suggestion_status(
        self,
        *,
        profile_id: UUID,
        suggestion_id: UUID,
        status: str,
        error: str | None = None,
    ) -> None:
        """Update one suggestion status and optional error rationale."""

    def create_shared_expense_from_suggestion(
        self,
        *,
        profile_id: UUID,
        suggestion_id: UUID,
        amount: Decimal,
    ) -> UUID | None:
        """Create one shared expense from a suggestion and mark suggestion as applied/failed."""

    def get_suggestion_by_id(
        self,
        *,
        profile_id: UUID,
        suggestion_id: UUID,
    ) -> SharedExpenseSuggestionRow | None:
        """Return one suggestion row for the given profile and suggestion id."""

    def list_shared_expenses_for_period(
        self,
        *,
        profile_id: UUID,
        start_date: date,
        end_date: date,
    ) -> list[SharedExpenseRow]:
        """Return pending/settled shared expenses where profile is payer or beneficiary for the period."""


class SupabaseSharedExpensesRepository:
    """Supabase-backed adapter for shared expenses feature."""

    def __init__(self, *, client: SupabaseClient) -> None:
        self._client = client

    def list_auto_share_categories(self, *, profile_id: UUID) -> list[dict[str, Any]]:
        rows, _ = self._client.get_rows(
            table="profile_categories",
            query={
                "select": (
                    "id,name,auto_share_enabled,auto_share_link_id,"
                    "auto_share_to_profile_id,auto_share_split_ratio_other"
                ),
                "profile_id": f"eq.{profile_id}",
                "scope": "eq.personal",
                "auto_share_enabled": "eq.true",
                "auto_share_link_id": "not.is.null",
                "auto_share_to_profile_id": "not.is.null",
                "limit": 300,
            },
            with_count=False,
            use_anon_key=False,
        )
        return rows

    def create_shared_expense_suggestions_bulk(self, *, profile_id: UUID, suggestions: list[dict[str, Any]]) -> int:
        if not suggestions:
            return 0

        payload = [
            {
                **row,
                "profile_id": str(profile_id),
                "transaction_id": str(row["transaction_id"]),
                "suggested_to_profile_id": str(row["suggested_to_profile_id"]) if row.get("suggested_to_profile_id") else None,
                "other_party_label": str(row["other_party_label"]) if row.get("other_party_label") else None,
                "link_id": str(row["link_id"]) if row.get("link_id") else None,
                "link_pair_id": str(row["link_pair_id"]) if row.get("link_pair_id") else None,
                "suggested_split_ratio_other": str(row.get("suggested_split_ratio_other", Decimal("0.5"))),
            }
            for row in suggestions
        ]

        try:
            inserted = self._client.post_rows(
                table="shared_expense_suggestions",
                payload=payload,
                use_anon_key=False,
            )
            return len(inserted)
        except RuntimeError as exc:
            if "duplicate key" not in str(exc).lower() and "unique" not in str(exc).lower():
                raise

        created_count = 0
        for row in payload:
            try:
                self._client.post_rows(
                    table="shared_expense_suggestions",
                    payload=row,
                    use_anon_key=False,
                )
                created_count += 1
            except RuntimeError as exc:
                if "duplicate key" in str(exc).lower() or "unique" in str(exc).lower():
                    continue
                raise
        return created_count

    def list_shared_expense_suggestions(
        self,
        *,
        profile_id: UUID,
        status: str = "pending",
        limit: int = 100,
    ) -> list[SharedExpenseSuggestionRow]:
        rows, _ = self._client.get_rows(
            table="shared_expense_suggestions",
            query={
                "select": (
                    "id,profile_id,transaction_id,suggested_to_profile_id,other_party_label,"
                    "suggested_split_ratio_other,status,confidence,rationale,link_id,link_pair_id"
                ),
                "profile_id": f"eq.{profile_id}",
                "status": f"eq.{status}",
                "order": "created_at.desc",
                "limit": limit,
            },
            with_count=False,
            use_anon_key=False,
        )
        return [self._map_suggestion_row(row) for row in rows]

    def mark_suggestion_status(
        self,
        *,
        profile_id: UUID,
        suggestion_id: UUID,
        status: str,
        error: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if error is not None:
            payload["rationale"] = error

        self._client.patch_rows(
            table="shared_expense_suggestions",
            query={
                "profile_id": f"eq.{profile_id}",
                "id": f"eq.{suggestion_id}",
            },
            payload=payload,
            use_anon_key=False,
        )

    def create_shared_expense_from_suggestion(
        self,
        *,
        profile_id: UUID,
        suggestion_id: UUID,
        amount: Decimal,
    ) -> UUID | None:
        rows, _ = self._client.get_rows(
            table="shared_expense_suggestions",
            query={
                "select": (
                    "id,profile_id,transaction_id,suggested_to_profile_id,other_party_label,"
                    "suggested_split_ratio_other,status,link_id,link_pair_id"
                ),
                "profile_id": f"eq.{profile_id}",
                "id": f"eq.{suggestion_id}",
                "limit": 1,
            },
            with_count=False,
            use_anon_key=False,
        )
        if not rows:
            return None

        suggestion = rows[0]
        expense_payload = {
            "from_profile_id": str(profile_id),
            "to_profile_id": str(suggestion["suggested_to_profile_id"]) if suggestion.get("suggested_to_profile_id") else None,
            "transaction_id": str(suggestion["transaction_id"]),
            "amount": str(amount),
            # shared_expenses.status is a DB enum and only accepts pending|settled.
            # shared_expense_suggestions.status is independent and can be set to "applied".
            "status": "pending",
            "split_ratio_other": str(suggestion.get("suggested_split_ratio_other") or "0.5"),
            "other_party_label": str(suggestion["other_party_label"]) if suggestion.get("other_party_label") else None,
            "link_id": str(suggestion["link_id"]) if suggestion.get("link_id") else None,
            "link_pair_id": str(suggestion["link_pair_id"]) if suggestion.get("link_pair_id") else None,
        }
        try:
            inserted = self._client.post_rows(
                table="shared_expenses",
                payload=expense_payload,
                use_anon_key=False,
            )
        except RuntimeError as exc:
            error_message = str(exc)
            if "shared_expenses" in error_message.lower() and (
                "does not exist" in error_message.lower() or "not found" in error_message.lower()
            ):
                self.mark_suggestion_status(
                    profile_id=profile_id,
                    suggestion_id=suggestion_id,
                    status="failed",
                    error="Table shared_expenses absente côté Supabase.",
                )
                return None
            raise

        created_id = UUID(str(inserted[0]["id"])) if inserted and inserted[0].get("id") else None
        self.mark_suggestion_status(profile_id=profile_id, suggestion_id=suggestion_id, status="applied")

        if created_id is not None:
            try:
                self._client.patch_rows(
                    table="releves_bancaires",
                    query={
                        "profile_id": f"eq.{profile_id}",
                        "id": f"eq.{suggestion['transaction_id']}",
                    },
                    payload={"shared_expense_id": str(created_id)},
                    use_anon_key=False,
                )
            except RuntimeError:
                pass

        return created_id

    def get_suggestion_by_id(
        self,
        *,
        profile_id: UUID,
        suggestion_id: UUID,
    ) -> SharedExpenseSuggestionRow | None:
        rows, _ = self._client.get_rows(
            table="shared_expense_suggestions",
            query={
                "select": (
                    "id,profile_id,transaction_id,suggested_to_profile_id,other_party_label,"
                    "suggested_split_ratio_other,status,confidence,rationale,link_id,link_pair_id"
                ),
                "profile_id": f"eq.{profile_id}",
                "id": f"eq.{suggestion_id}",
                "limit": 1,
            },
            with_count=False,
            use_anon_key=False,
        )
        if not rows:
            return None
        return self._map_suggestion_row(rows[0])

    def list_shared_expenses_for_period(
        self,
        *,
        profile_id: UUID,
        start_date: date,
        end_date: date,
    ) -> list[SharedExpenseRow]:
        try:
            rows, _ = self._client.get_rows(
                table="shared_expenses",
                query=[
                    (
                        "select",
                        "from_profile_id,to_profile_id,transaction_id,amount,created_at,status,split_ratio_other,other_party_label",
                    ),
                    ("or", f"(from_profile_id.eq.{profile_id},to_profile_id.eq.{profile_id})"),
                    ("status", "in.(pending,settled)"),
                    # TODO(MVP): created_at is only a proxy; ideally filter by underlying transaction date.
                    ("created_at", f"gte.{start_date.isoformat()}T00:00:00+00:00"),
                    ("created_at", f"lte.{end_date.isoformat()}T23:59:59+00:00"),
                    ("limit", 2000),
                ],
                with_count=False,
                use_anon_key=False,
            )
        except RuntimeError as exc:
            if "shared_expenses" in str(exc).lower() and (
                "does not exist" in str(exc).lower() or "not found" in str(exc).lower()
            ):
                return []
            raise

        mapped: list[SharedExpenseRow] = []
        for row in rows:
            raw_status = str(row.get("status") or "").strip().lower()
            if raw_status not in {"pending", "settled"}:
                raw_status = "pending"

            created_at = None
            created_raw = row.get("created_at")
            if isinstance(created_raw, str) and created_raw:
                try:
                    created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                except ValueError:
                    created_at = None

            mapped.append(
                SharedExpenseRow(
                    from_profile_id=UUID(str(row["from_profile_id"])),
                    to_profile_id=UUID(str(row["to_profile_id"])) if row.get("to_profile_id") else None,
                    transaction_id=UUID(str(row["transaction_id"])) if row.get("transaction_id") else None,
                    amount=Decimal(str(row.get("amount") or "0")),
                    created_at=created_at,
                    status=raw_status,
                    split_ratio_other=(
                        Decimal(str(row["split_ratio_other"]))
                        if row.get("split_ratio_other") is not None
                        else None
                    ),
                    other_party_label=str(row["other_party_label"]) if row.get("other_party_label") else None,
                )
            )
        return mapped

    @staticmethod
    def _map_suggestion_row(row: dict[str, Any]) -> SharedExpenseSuggestionRow:
        return SharedExpenseSuggestionRow(
            id=UUID(str(row["id"])),
            profile_id=UUID(str(row["profile_id"])),
            transaction_id=UUID(str(row["transaction_id"])),
            suggested_to_profile_id=(UUID(str(row["suggested_to_profile_id"])) if row.get("suggested_to_profile_id") else None),
            suggested_split_ratio_other=Decimal(str(row.get("suggested_split_ratio_other") or "0.5")),
            status=str(row.get("status") or "pending"),
            confidence=float(row["confidence"]) if row.get("confidence") is not None else None,
            rationale=str(row["rationale"]) if row.get("rationale") is not None else None,
            link_id=UUID(str(row["link_id"])) if row.get("link_id") else None,
            link_pair_id=UUID(str(row["link_pair_id"])) if row.get("link_pair_id") else None,
            other_party_label=str(row["other_party_label"]) if row.get("other_party_label") else None,
        )


class InMemorySharedExpensesRepository:
    """In-memory fallback repository for tests/dev without Supabase."""

    def __init__(self) -> None:
        self._suggestions: list[dict[str, Any]] = []
        self._shared_expenses: list[SharedExpenseRow] = []

    def list_auto_share_categories(self, *, profile_id: UUID) -> list[dict[str, Any]]:
        return []

    def create_shared_expense_suggestions_bulk(self, *, profile_id: UUID, suggestions: list[dict[str, Any]]) -> int:
        created_count = 0
        for suggestion in suggestions:
            suggested_to_profile_id = (
                UUID(str(suggestion["suggested_to_profile_id"])) if suggestion.get("suggested_to_profile_id") else None
            )
            other_party_label = str(suggestion["other_party_label"]) if suggestion.get("other_party_label") else None
            dedup_key = (
                profile_id,
                UUID(str(suggestion["transaction_id"])),
                suggested_to_profile_id,
                Decimal(str(suggestion.get("suggested_split_ratio_other") or "0.5")),
                other_party_label if suggested_to_profile_id is None else None,
            )
            already_pending = any(
                item["status"] == "pending"
                and (
                    UUID(str(item["profile_id"])),
                    UUID(str(item["transaction_id"])),
                    UUID(str(item["suggested_to_profile_id"])) if item.get("suggested_to_profile_id") else None,
                    Decimal(str(item.get("suggested_split_ratio_other") or "0.5")),
                    (str(item["other_party_label"]) if item.get("other_party_label") else None)
                    if item.get("suggested_to_profile_id") is None
                    else None,
                )
                == dedup_key
                for item in self._suggestions
            )
            if already_pending:
                continue

            row = {
                "id": uuid4(),
                "profile_id": profile_id,
                "transaction_id": UUID(str(suggestion["transaction_id"])),
                "suggested_to_profile_id": (
                    UUID(str(suggestion["suggested_to_profile_id"])) if suggestion.get("suggested_to_profile_id") else None
                ),
                "suggested_split_ratio_other": Decimal(str(suggestion.get("suggested_split_ratio_other") or "0.5")),
                "status": str(suggestion.get("status") or "pending"),
                "confidence": float(suggestion["confidence"]) if suggestion.get("confidence") is not None else None,
                "rationale": suggestion.get("rationale"),
                "link_id": UUID(str(suggestion["link_id"])) if suggestion.get("link_id") else None,
                "link_pair_id": UUID(str(suggestion["link_pair_id"])) if suggestion.get("link_pair_id") else None,
                "other_party_label": str(suggestion["other_party_label"]) if suggestion.get("other_party_label") else None,
                "updated_at": datetime.now(timezone.utc),
            }
            self._suggestions.append(row)
            created_count += 1
        return created_count

    def list_shared_expense_suggestions(
        self,
        *,
        profile_id: UUID,
        status: str = "pending",
        limit: int = 100,
    ) -> list[SharedExpenseSuggestionRow]:
        items = [
            row
            for row in self._suggestions
            if row["profile_id"] == profile_id and str(row.get("status") or "") == status
        ]
        return [
            SharedExpenseSuggestionRow(
                id=row["id"],
                profile_id=row["profile_id"],
                transaction_id=row["transaction_id"],
                suggested_to_profile_id=row["suggested_to_profile_id"],
                suggested_split_ratio_other=row["suggested_split_ratio_other"],
                status=row["status"],
                confidence=row["confidence"],
                rationale=row.get("rationale"),
                link_id=row.get("link_id"),
                link_pair_id=row.get("link_pair_id"),
                other_party_label=row.get("other_party_label"),
            )
            for row in items[:limit]
        ]

    def mark_suggestion_status(
        self,
        *,
        profile_id: UUID,
        suggestion_id: UUID,
        status: str,
        error: str | None = None,
    ) -> None:
        for row in self._suggestions:
            if row["profile_id"] == profile_id and row["id"] == suggestion_id:
                row["status"] = status
                row["updated_at"] = datetime.now(timezone.utc)
                if error is not None:
                    row["rationale"] = error
                return

    def create_shared_expense_from_suggestion(
        self,
        *,
        profile_id: UUID,
        suggestion_id: UUID,
        amount: Decimal,
    ) -> UUID | None:
        for row in self._suggestions:
            if row["profile_id"] != profile_id or row["id"] != suggestion_id:
                continue

            shared_row = SharedExpenseRow(
                from_profile_id=profile_id,
                to_profile_id=row["suggested_to_profile_id"],
                transaction_id=row["transaction_id"],
                amount=Decimal(str(amount)),
                created_at=datetime.now(timezone.utc),
                status="pending",
                split_ratio_other=row.get("suggested_split_ratio_other"),
                other_party_label=row.get("other_party_label"),
            )
            self._shared_expenses.append(shared_row)
            # This status belongs to shared_expense_suggestions and is independent from shared_expenses.status.
            row["status"] = "applied"
            return uuid4()
        return None

    def list_shared_expenses_for_period(
        self,
        *,
        profile_id: UUID,
        start_date: date,
        end_date: date,
    ) -> list[SharedExpenseRow]:
        filtered: list[SharedExpenseRow] = []
        for row in self._shared_expenses:
            if row.status not in {"pending", "settled"}:
                continue
            if row.from_profile_id != profile_id and row.to_profile_id != profile_id:
                continue
            if row.created_at is None:
                continue
            if not (start_date <= row.created_at.date() <= end_date):
                continue
            filtered.append(row)
        return filtered

    def get_suggestion_by_id(
        self,
        *,
        profile_id: UUID,
        suggestion_id: UUID,
    ) -> SharedExpenseSuggestionRow | None:
        for row in self._suggestions:
            if row["profile_id"] != profile_id or row["id"] != suggestion_id:
                continue
            return SharedExpenseSuggestionRow(
                id=row["id"],
                profile_id=row["profile_id"],
                transaction_id=row["transaction_id"],
                suggested_to_profile_id=row["suggested_to_profile_id"],
                suggested_split_ratio_other=row["suggested_split_ratio_other"],
                status=row["status"],
                confidence=row["confidence"],
                rationale=row.get("rationale"),
                link_id=row.get("link_id"),
                link_pair_id=row.get("link_pair_id"),
                other_party_label=row.get("other_party_label"),
            )
        return None

    def seed_shared_expenses(self, rows: list[SharedExpenseRow]) -> None:
        """Seed shared expenses rows for tests."""

        self._shared_expenses.extend(rows)
