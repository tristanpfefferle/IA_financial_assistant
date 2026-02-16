"""Compatibility tests for deprecated transaction tool aliases."""

from uuid import UUID

from agent.tool_router import ToolRouter
from shared.models import RelevesSumResult
from tests.fakes import FakeBackendClient

PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def test_transactions_sum_alias_matches_releves_sum() -> None:
    tool_router = ToolRouter(backend_client=FakeBackendClient())

    payload = {"direction": "DEBIT_ONLY", "limit": 50, "offset": 0}
    tx_result = tool_router.call(
        "finance_transactions_sum", payload, profile_id=PROFILE_ID
    )
    releves_result = tool_router.call(
        "finance_releves_sum", payload, profile_id=PROFILE_ID
    )

    assert isinstance(tx_result, RelevesSumResult)
    assert isinstance(releves_result, RelevesSumResult)
    assert tx_result.total == releves_result.total
    assert tx_result.count == releves_result.count
    assert tx_result.currency == releves_result.currency
