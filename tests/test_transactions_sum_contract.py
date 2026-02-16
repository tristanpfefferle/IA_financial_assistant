"""Contract tests for finance_transactions_sum aliasing to releves."""

from datetime import date
from decimal import Decimal
from uuid import UUID

from agent.tool_router import ToolRouter
from shared.models import (
    DateRange,
    ReleveBancaire,
    RelevesDirection,
    RelevesFilters,
    RelevesSumResult,
    ToolError,
    ToolErrorCode,
    TransactionSearchResult,
    TransactionSumResult,
)
from tests.fakes import FakeBackendClient

PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def test_sum_all_returns_decimal_and_count() -> None:
    tool_router = ToolRouter(backend_client=FakeBackendClient())

    result = tool_router.call("finance_transactions_sum", {}, profile_id=PROFILE_ID)

    assert isinstance(result, TransactionSumResult)
    assert isinstance(result.total, Decimal)
    assert result.count >= 1


def test_sum_debit_only_is_negative_or_zero_and_count_matches() -> None:
    tool_router = ToolRouter(backend_client=FakeBackendClient())

    debit_result = tool_router.call("finance_transactions_sum", {"direction": "DEBIT_ONLY"}, profile_id=PROFILE_ID)
    credit_result = tool_router.call(
        "finance_transactions_sum", {"direction": "CREDIT_ONLY"}, profile_id=PROFILE_ID
    )
    base_result = tool_router.call("finance_transactions_search", {}, profile_id=PROFILE_ID)

    assert isinstance(debit_result, TransactionSumResult)
    assert isinstance(credit_result, TransactionSumResult)
    assert isinstance(base_result, TransactionSearchResult)
    assert debit_result.total <= Decimal("0")
    assert credit_result.total >= Decimal("0")
    assert debit_result.count == len([tx for tx in base_result.items if tx.montant < 0])


def test_sum_with_merchant_filter() -> None:
    tool_router = ToolRouter(backend_client=FakeBackendClient())

    result = tool_router.call("finance_transactions_sum", {"merchant": "coffee"}, profile_id=PROFILE_ID)

    assert isinstance(result, TransactionSumResult)
    assert result.count == 1
    assert result.total == Decimal("-12.30")


def test_sum_invalid_direction_validation_error() -> None:
    tool_router = ToolRouter(backend_client=FakeBackendClient())

    result = tool_router.call("finance_transactions_sum", {"direction": "WRONG"}, profile_id=PROFILE_ID)

    assert isinstance(result, ToolError)
    assert result.code == ToolErrorCode.VALIDATION_ERROR


class _MerchantDateRangeBackendClient:
    def releves_sum(self, filters: RelevesFilters) -> RelevesSumResult:
        items = [
            ReleveBancaire(
                id=UUID("aaaaaaaa-1111-1111-1111-111111111111"),
                profile_id=PROFILE_ID,
                date=date(2026, 1, 5),
                montant=Decimal("-20.00"),
                devise="CHF",
                payee="COOP Nation",
            ),
            ReleveBancaire(
                id=UUID("aaaaaaaa-2222-2222-2222-222222222222"),
                profile_id=PROFILE_ID,
                date=date(2026, 2, 5),
                montant=Decimal("-35.00"),
                devise="CHF",
                payee="COOP Cornavin",
            ),
        ]

        if filters.merchant:
            needle = filters.merchant.lower()
            items = [item for item in items if item.payee and needle in item.payee.lower()]

        if filters.direction == RelevesDirection.DEBIT_ONLY:
            items = [item for item in items if item.montant < 0]

        if filters.date_range is not None:
            start_date = filters.date_range.start_date
            end_date = filters.date_range.end_date
            items = [item for item in items if start_date <= item.date <= end_date]

        count = len(items)
        total = sum((item.montant for item in items), start=Decimal("0"))
        average = total / count if count > 0 else Decimal("0")
        return RelevesSumResult(total=total, count=count, average=average, currency="CHF", filters=filters)


def test_sum_with_merchant_and_date_range_filters_are_composed() -> None:
    tool_router = ToolRouter(backend_client=_MerchantDateRangeBackendClient())

    result = tool_router.call(
        "finance_transactions_sum",
        {
            "merchant": "coop",
            "direction": "DEBIT_ONLY",
            "date_range": DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 31)),
        },
        profile_id=PROFILE_ID,
    )

    assert isinstance(result, TransactionSumResult)
    assert result.count == 1
    assert result.total == Decimal("-20.00")
