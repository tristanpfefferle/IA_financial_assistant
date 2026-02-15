"""Repository interfaces for transactions."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol

from shared.models import (
    Money,
    Transaction,
    TransactionFilters,
    TransactionSumDirection,
)


class TransactionsRepository(Protocol):
    def list_transactions(self, filters: TransactionFilters) -> list[Transaction]:
        """Return transactions using typed filters."""

    def sum_transactions(self, filters: TransactionFilters) -> tuple[Decimal, int, str]:
        """Return aggregated amount, count and currency using typed filters."""


class GestionFinanciereTransactionsRepository:
    """Dev/test in-memory repository.

    TODO: Replace with an adapter around `gestion_financiere` source-of-truth
    transaction access functions when backend integration is wired.
    """

    def __init__(self) -> None:
        self._seed: list[Transaction] = [
            Transaction(
                id="tx_1",
                account_id="acc_main",
                category_id="cat_food",
                description="Supermarket groceries",
                amount=Money(amount=Decimal("-54.20"), currency="EUR"),
                booked_at=datetime.fromisoformat("2025-01-10T09:00:00"),
            ),
            Transaction(
                id="tx_2",
                account_id="acc_main",
                category_id="cat_transport",
                description="Monthly train pass",
                amount=Money(amount=Decimal("-78.00"), currency="EUR"),
                booked_at=datetime.fromisoformat("2025-01-02T08:15:00"),
            ),
            Transaction(
                id="tx_3",
                account_id="acc_savings",
                category_id="cat_income",
                description="Salary January",
                amount=Money(amount=Decimal("2400.00"), currency="EUR"),
                booked_at=datetime.fromisoformat("2025-01-01T12:00:00"),
            ),
            Transaction(
                id="tx_4",
                account_id="acc_main",
                category_id="cat_food",
                description="Coffee beans",
                amount=Money(amount=Decimal("-12.30"), currency="EUR"),
                booked_at=datetime.fromisoformat("2025-01-11T14:30:00"),
            ),
        ]

    def _apply_filters_without_pagination(self, filters: TransactionFilters) -> list[Transaction]:
        items = self._seed

        if filters.account_id:
            known_account = any(tx.account_id == filters.account_id for tx in items)
            if known_account:
                items = [tx for tx in items if tx.account_id == filters.account_id]

        if filters.category_id:
            known_category = any(tx.category_id == filters.category_id for tx in items)
            if known_category:
                items = [tx for tx in items if tx.category_id == filters.category_id]

        if filters.search:
            lowered_search = filters.search.lower()
            items = [tx for tx in items if lowered_search in tx.description.lower()]

        if filters.date_range:
            start = filters.date_range.start_date
            end = filters.date_range.end_date
            items = [tx for tx in items if start <= tx.booked_at.date() <= end]

        if filters.min_amount is not None:
            items = [tx for tx in items if tx.amount.amount >= filters.min_amount]

        if filters.max_amount is not None:
            items = [tx for tx in items if tx.amount.amount <= filters.max_amount]

        return items

    def list_transactions(self, filters: TransactionFilters) -> list[Transaction]:
        items = self._apply_filters_without_pagination(filters)
        offset = filters.offset
        limit = filters.limit
        return items[offset : offset + limit]

    def sum_transactions(self, filters: TransactionFilters) -> tuple[Decimal, int, str]:
        items = self._apply_filters_without_pagination(filters)

        if filters.direction == TransactionSumDirection.DEBIT_ONLY:
            items = [tx for tx in items if tx.amount.amount < 0]
        elif filters.direction == TransactionSumDirection.CREDIT_ONLY:
            items = [tx for tx in items if tx.amount.amount > 0]

        total = sum((tx.amount.amount for tx in items), Decimal("0"))
        currency = items[0].amount.currency if items else "EUR"
        return total, len(items), currency
