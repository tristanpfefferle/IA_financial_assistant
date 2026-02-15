"""Contract tests for finance.transactions.sum."""

from decimal import Decimal

from agent.factory import build_agent_loop
from shared.models import ToolError, ToolErrorCode, TransactionSearchResult, TransactionSumResult


def test_sum_all_returns_decimal_and_count() -> None:
    agent_loop = build_agent_loop()

    result = agent_loop.tool_router.call("finance.transactions.sum", {})

    assert isinstance(result, TransactionSumResult)
    assert isinstance(result.total.amount, Decimal)
    assert result.count >= 1


def test_sum_debit_only_is_negative_or_zero_and_count_matches() -> None:
    agent_loop = build_agent_loop()

    debit_result = agent_loop.tool_router.call("finance.transactions.sum", {"direction": "DEBIT_ONLY"})
    base_result = agent_loop.tool_router.call("finance.transactions.search", {})

    assert isinstance(debit_result, TransactionSumResult)
    assert isinstance(base_result, TransactionSearchResult)
    assert debit_result.total.amount <= Decimal("0")
    assert debit_result.count == len([tx for tx in base_result.items if tx.amount.amount < 0])


def test_sum_with_search_filter() -> None:
    agent_loop = build_agent_loop()

    result = agent_loop.tool_router.call("finance.transactions.sum", {"search": "coffee"})

    assert isinstance(result, TransactionSumResult)
    assert result.count == 1
    assert result.total.amount == Decimal("-12.30")


def test_sum_invalid_direction_validation_error() -> None:
    agent_loop = build_agent_loop()

    result = agent_loop.tool_router.call("finance.transactions.sum", {"direction": "WRONG"})

    assert isinstance(result, ToolError)
    assert result.code == ToolErrorCode.VALIDATION_ERROR
