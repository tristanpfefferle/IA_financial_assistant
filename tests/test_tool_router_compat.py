"""Compatibility tests for deprecated transaction tool aliases."""

from uuid import UUID

from agent.factory import build_agent_loop
from shared.models import RelevesSumResult

PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def test_transactions_sum_alias_matches_releves_sum() -> None:
    agent_loop = build_agent_loop()

    payload = {"direction": "DEBIT_ONLY", "limit": 50, "offset": 0}
    tx_result = agent_loop.tool_router.call(
        "finance_transactions_sum", payload, profile_id=PROFILE_ID
    )
    releves_result = agent_loop.tool_router.call(
        "finance_releves_sum", payload, profile_id=PROFILE_ID
    )

    assert isinstance(tx_result, RelevesSumResult)
    assert isinstance(releves_result, RelevesSumResult)
    assert tx_result == releves_result
