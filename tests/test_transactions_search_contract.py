"""Contract tests for finance.transactions.search."""

from agent.factory import build_agent_loop
from shared.models import ToolError, TransactionSearchResult


def test_transactions_search_returns_paginated_result() -> None:
    agent_loop = build_agent_loop()

    payload = {"limit": 2, "offset": 1}
    result = agent_loop.tool_router.call("finance.transactions.search", payload)

    assert isinstance(result, TransactionSearchResult)
    assert result.limit == 2
    assert result.offset == 1
    assert len(result.items) <= 2


def test_transactions_search_applies_search_filter() -> None:
    agent_loop = build_agent_loop()

    base_result = agent_loop.tool_router.call(
        "finance.transactions.search",
        {"limit": 50, "offset": 0},
    )
    filtered_result = agent_loop.tool_router.call(
        "finance.transactions.search",
        {"search": "coffee", "limit": 50, "offset": 0},
    )

    assert isinstance(base_result, TransactionSearchResult)
    assert isinstance(filtered_result, TransactionSearchResult)
    assert 0 < len(filtered_result.items) < len(base_result.items)
    assert all("coffee" in transaction.description.lower() for transaction in filtered_result.items)


def test_transactions_search_returns_tool_error_for_invalid_payload() -> None:
    agent_loop = build_agent_loop()

    result = agent_loop.tool_router.call("finance.transactions.search", {"limit": "invalid"})

    assert isinstance(result, ToolError)
