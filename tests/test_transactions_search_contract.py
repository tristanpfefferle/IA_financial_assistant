"""Contract tests for finance_transactions_search aliasing to releves."""

from uuid import UUID

from agent.factory import build_agent_loop
from shared.models import ToolError, TransactionSearchResult

PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def test_transactions_search_returns_paginated_result() -> None:
    agent_loop = build_agent_loop()

    payload = {"limit": 2, "offset": 1}
    result = agent_loop.tool_router.call("finance_transactions_search", payload, profile_id=PROFILE_ID)

    assert isinstance(result, TransactionSearchResult)
    assert result.limit == 2
    assert result.offset == 1
    assert len(result.items) <= 2


def test_transactions_search_applies_merchant_filter() -> None:
    agent_loop = build_agent_loop()

    base_result = agent_loop.tool_router.call(
        "finance_transactions_search",
        {"limit": 50, "offset": 0},
        profile_id=PROFILE_ID,
    )
    filtered_result = agent_loop.tool_router.call(
        "finance_transactions_search",
        {"merchant": "coffee", "limit": 50, "offset": 0},
        profile_id=PROFILE_ID,
    )

    assert isinstance(base_result, TransactionSearchResult)
    assert isinstance(filtered_result, TransactionSearchResult)
    assert 0 < len(filtered_result.items) < len(base_result.items)
    assert all("coffee" in (transaction.payee or "").lower() for transaction in filtered_result.items)


def test_transactions_search_returns_tool_error_for_invalid_payload() -> None:
    agent_loop = build_agent_loop()

    result = agent_loop.tool_router.call(
        "finance_transactions_search", {"limit": "invalid"}, profile_id=PROFILE_ID
    )

    assert isinstance(result, ToolError)
