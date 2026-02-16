"""Tests for profiles repository account_id/email resolution."""

from __future__ import annotations

from uuid import UUID

from backend.repositories.profiles_repository import SupabaseProfilesRepository


class _ClientStub:
    def __init__(self, responses: list[list[dict[str, str]]]) -> None:
        self._responses = responses
        self.calls: list[dict[str, object]] = []
        self.patch_calls: list[dict[str, object]] = []

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

    def patch_rows(self, *, table, query, payload, use_anon_key=False):
        self.patch_calls.append(
            {
                "table": table,
                "query": query,
                "payload": payload,
                "use_anon_key": use_anon_key,
            }
        )
        return []


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



def test_get_chat_state_returns_empty_dict_when_null() -> None:
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    client = _ClientStub(responses=[[{"chat_state": None}]])
    repository = SupabaseProfilesRepository(client=client)

    chat_state = repository.get_chat_state(profile_id=profile_id)

    assert chat_state == {}
    assert client.calls[0]["query"] == {"select": "chat_state", "id": f"eq.{profile_id}", "limit": 1}


def test_update_chat_state_patches_profile_row() -> None:
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    client = _ClientStub(responses=[])
    repository = SupabaseProfilesRepository(client=client)

    repository.update_chat_state(profile_id=profile_id, chat_state={"active_task": {"type": "x"}})

    assert client.patch_calls == [
        {
            "table": "profils",
            "query": {"id": f"eq.{profile_id}"},
            "payload": {"chat_state": {"active_task": {"type": "x"}}},
            "use_anon_key": False,
        }
    ]
