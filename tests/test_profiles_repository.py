"""Tests for profiles repository account_id/email resolution."""

from __future__ import annotations

from uuid import UUID

from backend.repositories.profiles_repository import SupabaseProfilesRepository


class _ClientStub:
    def __init__(self, responses: list[list[dict[str, str]]]) -> None:
        self._responses = responses
        self.calls: list[dict[str, object]] = []

    def get_rows(self, *, table, query, with_count, use_anon_key=False):
        self.calls.append(
            {
                "table": table,
                "query": query,
                "with_count": with_count,
                "use_anon_key": use_anon_key,
            }
        )
        return self._responses[len(self.calls) - 1], None


def test_get_profile_id_for_auth_user_prefers_account_id() -> None:
    auth_user_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    expected_profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    client = _ClientStub(responses=[[{"id": str(expected_profile_id)}]])

    repository = SupabaseProfilesRepository(client=client)

    profile_id = repository.get_profile_id_for_auth_user(auth_user_id=auth_user_id, email="user@example.com")

    assert profile_id == expected_profile_id
    assert len(client.calls) == 1
    assert client.calls[0]["table"] == "profils"
    assert client.calls[0]["query"] == {
        "select": "id",
        "account_id": f"eq.{auth_user_id}",
        "limit": 1,
    }
    assert client.calls[0]["use_anon_key"] is False
    assert "and" not in client.calls[0]["query"]


def test_get_profile_id_for_auth_user_falls_back_to_email() -> None:
    auth_user_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    expected_profile_id = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
    client = _ClientStub(responses=[[], [{"id": str(expected_profile_id)}]])

    repository = SupabaseProfilesRepository(client=client)

    profile_id = repository.get_profile_id_for_auth_user(auth_user_id=auth_user_id, email="user@example.com")

    assert profile_id == expected_profile_id
    assert len(client.calls) == 2
    assert client.calls[0]["query"] == {"select": "id", "account_id": f"eq.{auth_user_id}", "limit": 1}
    assert client.calls[1]["query"] == {"select": "id", "email": "eq.user@example.com", "limit": 1}
    assert all(call["use_anon_key"] is False for call in client.calls)


def test_get_profile_id_for_auth_user_returns_none_when_no_match() -> None:
    auth_user_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    client = _ClientStub(responses=[[]])

    repository = SupabaseProfilesRepository(client=client)

    profile_id = repository.get_profile_id_for_auth_user(auth_user_id=auth_user_id, email=None)

    assert profile_id is None
    assert len(client.calls) == 1
