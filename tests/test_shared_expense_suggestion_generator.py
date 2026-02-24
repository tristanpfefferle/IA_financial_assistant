from decimal import Decimal
from uuid import UUID, uuid4

from backend.repositories.shared_expenses_repository import InMemorySharedExpensesRepository
from backend.services.shared_expenses.suggestion_generator import (
    MIN_CONFIDENCE_THRESHOLD,
    compute_share_confidence,
    generate_initial_shared_expense_suggestions,
)


class _FakeSupabaseClient:
    def __init__(self, rows):
        self.rows = rows

    def get_rows(self, *, table, query, with_count, use_anon_key):
        return self.rows, None


def test_compute_share_confidence_shareable_category_high_amount() -> None:
    confidence, rationale = compute_share_confidence(category_norm="food", amount=Decimal("50"))

    assert confidence >= MIN_CONFIDENCE_THRESHOLD
    assert "shareable_category" in rationale


def test_compute_share_confidence_personal_category() -> None:
    confidence, _ = compute_share_confidence(category_norm="habits", amount=Decimal("50"))

    assert confidence < MIN_CONFIDENCE_THRESHOLD


def test_suggestion_skipped_below_threshold() -> None:
    profile_id = uuid4()
    repository = InMemorySharedExpensesRepository()
    supabase_client = _FakeSupabaseClient(
        rows=[
            {
                "id": str(uuid4()),
                "montant": "-50.00",
                "payee": "Coffee shop",
                "libelle": "Morning routine",
                "categorie": "Habits",
                "category_norm": "habits",
                "date": "2026-02-24",
            }
        ]
    )

    created = generate_initial_shared_expense_suggestions(
        profile_id=profile_id,
        household_link={"link_type": "external", "other_party_label": "Coloc"},
        shared_expenses_repository=repository,
        supabase_client=supabase_client,
    )

    assert created == 0


def test_suggestion_created_above_threshold() -> None:
    profile_id = uuid4()
    repository = InMemorySharedExpensesRepository()
    transaction_id = uuid4()
    supabase_client = _FakeSupabaseClient(
        rows=[
            {
                "id": str(transaction_id),
                "montant": "-40.00",
                "payee": "Grocery Store",
                "libelle": "Weekly groceries",
                "categorie": "Food",
                "category_norm": "food",
                "date": "2026-02-24",
            }
        ]
    )

    created = generate_initial_shared_expense_suggestions(
        profile_id=profile_id,
        household_link={"link_type": "external", "other_party_label": "Coloc"},
        shared_expenses_repository=repository,
        supabase_client=supabase_client,
    )

    assert created == 1
    suggestions = repository.list_shared_expense_suggestions(profile_id=profile_id, status="pending", limit=10)
    assert len(suggestions) == 1
    assert suggestions[0].transaction_id == UUID(str(transaction_id))
    assert suggestions[0].confidence is not None
    assert suggestions[0].confidence >= MIN_CONFIDENCE_THRESHOLD
    assert suggestions[0].rationale is not None
