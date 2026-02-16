"""Compatibility tests for tool aliases and tool router payload contracts."""

from datetime import datetime, timezone
from uuid import UUID

from agent.tool_router import ToolRouter
from shared.models import ProfileCategory, RelevesAggregateResult, RelevesSumResult, ToolError, ToolErrorCode
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


def test_releves_aggregate_requires_profile_id_context() -> None:
    tool_router = ToolRouter(backend_client=FakeBackendClient())

    result = tool_router.call("finance_releves_aggregate", {"group_by": "categorie"})

    assert isinstance(result, ToolError)
    assert result.code == ToolErrorCode.VALIDATION_ERROR


def test_releves_aggregate_routes_to_backend_client() -> None:
    tool_router = ToolRouter(backend_client=FakeBackendClient())

    result = tool_router.call(
        "finance_releves_aggregate",
        {"group_by": "payee", "direction": "DEBIT_ONLY"},
        profile_id=PROFILE_ID,
    )

    assert isinstance(result, RelevesAggregateResult)
    assert result.group_by.value == "payee"


def test_categories_list_routes_to_backend_client() -> None:
    backend = FakeBackendClient(
        categories=[
            ProfileCategory(
                id=UUID("44444444-4444-4444-4444-444444444444"),
                profile_id=PROFILE_ID,
                name="Transfert interne",
                name_norm="transfert interne",
                exclude_from_totals=True,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        ]
    )
    tool_router = ToolRouter(backend_client=backend)

    result = tool_router.call("finance_categories_list", {}, profile_id=PROFILE_ID)

    assert hasattr(result, "items")
    assert result.items[0].name == "Transfert interne"


def test_categories_update_supports_lookup_by_category_name() -> None:
    backend = FakeBackendClient(
        categories=[
            ProfileCategory(
                id=UUID("44444444-4444-4444-4444-444444444444"),
                profile_id=PROFILE_ID,
                name="Transfert interne",
                name_norm="transfert interne",
                exclude_from_totals=False,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        ]
    )
    tool_router = ToolRouter(backend_client=backend)

    result = tool_router.call(
        "finance_categories_update",
        {"category_name": "  Transfert   interne  ", "exclude_from_totals": True},
        profile_id=PROFILE_ID,
    )

    assert isinstance(result, ProfileCategory)
    assert result.exclude_from_totals is True


def test_categories_delete_validates_payload() -> None:
    tool_router = ToolRouter(backend_client=FakeBackendClient())

    result = tool_router.call(
        "finance_categories_delete",
        {"foo": "bar"},
        profile_id=PROFILE_ID,
    )

    assert isinstance(result, ToolError)
    assert result.code == ToolErrorCode.VALIDATION_ERROR


def test_categories_update_by_name_not_found_returns_close_matches() -> None:
    backend = FakeBackendClient(
        categories=[
            ProfileCategory(
                id=UUID("44444444-4444-4444-4444-444444444444"),
                profile_id=PROFILE_ID,
                name="Transfert interne",
                name_norm="transfert interne",
                exclude_from_totals=False,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        ]
    )
    tool_router = ToolRouter(backend_client=backend)

    result = tool_router.call(
        "finance_categories_update",
        {"category_name": "transfret interne", "exclude_from_totals": True},
        profile_id=PROFILE_ID,
    )

    assert isinstance(result, ToolError)
    assert result.code == ToolErrorCode.NOT_FOUND
    assert result.details is not None
    assert result.details.get("category_name_norm") == "transfret interne"
    assert result.details.get("close_category_names") == ["Transfert interne"]
