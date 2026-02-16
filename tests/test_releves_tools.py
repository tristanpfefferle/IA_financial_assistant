"""Contract tests for finance_releves_* tools."""

from decimal import Decimal

from agent.factory import build_agent_loop
from shared.models import RelevesAggregateResult, RelevesSumResult, ToolError, ToolErrorCode


def test_releves_search_requires_profile_id_context() -> None:
    agent_loop = build_agent_loop()

    result = agent_loop.tool_router.call("finance_releves_search", {"limit": 10, "offset": 0})

    assert isinstance(result, ToolError)
    assert result.code == ToolErrorCode.VALIDATION_ERROR


def test_releves_sum_uses_profile_id_from_context() -> None:
    agent_loop = build_agent_loop()

    result = agent_loop.tool_router.call(
        "finance_releves_sum",
        {
            "direction": "ALL",
            "limit": 50,
            "offset": 0,
        },
        profile_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    )

    assert isinstance(result, RelevesSumResult)
    assert isinstance(result.total, Decimal)
    assert result.count >= 0


def test_aggregate_requires_profile_id_context() -> None:
    agent_loop = build_agent_loop()

    result = agent_loop.tool_router.call("finance_releves_aggregate", {"group_by": "categorie"})

    assert isinstance(result, ToolError)
    assert result.code == ToolErrorCode.VALIDATION_ERROR


def test_aggregate_by_category_works() -> None:
    agent_loop = build_agent_loop()

    result = agent_loop.tool_router.call(
        "finance_releves_aggregate",
        {
            "group_by": "categorie",
            "direction": "DEBIT_ONLY",
        },
        profile_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    )

    assert isinstance(result, RelevesAggregateResult)
    assert "alimentation" in result.groups
    assert result.groups["alimentation"].count == 2
    assert result.groups["alimentation"].total == Decimal("-66.50")
    assert result.currency == "EUR"


def test_sum_debit_only_excludes_excluded_categories() -> None:
    agent_loop = build_agent_loop()

    result = agent_loop.tool_router.call(
        "finance_releves_sum",
        {
            "direction": "DEBIT_ONLY",
            "limit": 50,
            "offset": 0,
        },
        profile_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    )

    assert isinstance(result, RelevesSumResult)
    assert result.total == Decimal("-966.50")
    assert result.count == 3


def test_aggregate_debit_only_excludes_excluded_categories() -> None:
    agent_loop = build_agent_loop()

    result = agent_loop.tool_router.call(
        "finance_releves_aggregate",
        {
            "group_by": "categorie",
            "direction": "DEBIT_ONLY",
        },
        profile_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    )

    assert isinstance(result, RelevesAggregateResult)
    assert "Transfert interne" not in result.groups
    assert "Logement" in result.groups
