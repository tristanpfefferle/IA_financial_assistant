"""Contract tests for finance_transactions_sum aliasing to releves."""

from decimal import Decimal
from uuid import UUID

from agent.tool_router import ToolRouter
from shared.models import ToolError, ToolErrorCode, TransactionSearchResult, TransactionSumResult
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
