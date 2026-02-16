"""Contract tests for finance_profile_* tools in the tool router."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from agent.tool_router import ToolRouter
from shared.models import ProfileDataResult, ToolError, ToolErrorCode
from tests.fakes import FakeBackendClient

PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def test_finance_profile_update_then_get_returns_same_fields() -> None:
    backend = FakeBackendClient(profile_data_by_id={PROFILE_ID: {}})
    router = ToolRouter(backend_client=backend)

    update_result = router.call(
        "finance_profile_update",
        {
            "set": {
                "first_name": "Paul",
                "city": "Bouveret",
                "birth_date": "2001-07-14",
            }
        },
        profile_id=PROFILE_ID,
    )

    assert isinstance(update_result, ProfileDataResult)
    assert update_result.data == {
        "first_name": "Paul",
        "city": "Bouveret",
        "birth_date": date(2001, 7, 14),
    }

    get_result = router.call(
        "finance_profile_get",
        {"fields": ["first_name", "city", "birth_date"]},
        profile_id=PROFILE_ID,
    )

    assert isinstance(get_result, ProfileDataResult)
    assert get_result.data == {
        "first_name": "Paul",
        "city": "Bouveret",
        "birth_date": date(2001, 7, 14),
    }


def test_finance_profile_update_rejects_non_whitelisted_field() -> None:
    router = ToolRouter(backend_client=FakeBackendClient())

    result = router.call(
        "finance_profile_update",
        {"set": {"donnees": {"foo": "bar"}}},
        profile_id=PROFILE_ID,
    )

    assert isinstance(result, ToolError)
    assert result.code == ToolErrorCode.VALIDATION_ERROR


def test_finance_profile_get_supports_french_alias_field_name() -> None:
    backend = FakeBackendClient(profile_data_by_id={PROFILE_ID: {"city": "Lausanne"}})
    router = ToolRouter(backend_client=backend)

    result = router.call("finance_profile_get", {"fields": ["ville"]}, profile_id=PROFILE_ID)

    assert isinstance(result, ProfileDataResult)
    assert result.data == {"city": "Lausanne"}


def test_finance_profile_get_unknown_field_returns_validation_error() -> None:
    router = ToolRouter(backend_client=FakeBackendClient())

    result = router.call("finance_profile_get", {"fields": ["couleur préférée"]}, profile_id=PROFILE_ID)

    assert isinstance(result, ToolError)
    assert result.code == ToolErrorCode.VALIDATION_ERROR
    assert result.details == {"field": "couleur préférée"}
