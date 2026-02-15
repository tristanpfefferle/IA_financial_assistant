"""Contract tests for finance_releves_* tools."""

from decimal import Decimal

from agent.factory import build_agent_loop
from shared.models import RelevesSumResult, ToolError, ToolErrorCode


def test_releves_search_requires_profile_id() -> None:
    agent_loop = build_agent_loop()

    result = agent_loop.tool_router.call("finance_releves_search", {"limit": 10, "offset": 0})

    assert isinstance(result, ToolError)
    assert result.code == ToolErrorCode.VALIDATION_ERROR


def test_releves_sum_returns_decimal_and_non_negative_count() -> None:
    agent_loop = build_agent_loop()

    result = agent_loop.tool_router.call(
        "finance_releves_sum",
        {
            "profile_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "direction": "ALL",
            "limit": 50,
            "offset": 0,
        },
    )

    assert isinstance(result, RelevesSumResult)
    assert isinstance(result.total, Decimal)
    assert result.count >= 0
