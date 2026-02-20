"""Tests for profiles repository account_id/email resolution."""

from __future__ import annotations

from uuid import UUID

from backend.repositories.profiles_repository import SupabaseProfilesRepository


class _ClientStub:
    def __init__(
        self,
        responses: list[list[dict[str, str]]],
        counts: list[int | None] | None = None,
        patch_responses: list[list[dict[str, str]]] | None = None,
        post_responses: list[list[dict[str, str]]] | None = None,
        post_exceptions: list[Exception] | None = None,
        delete_responses: list[list[dict[str, str]]] | None = None,
    ) -> None:
        self._responses = responses
        self._counts = counts or []
        self._patch_responses = patch_responses or []
        self._post_responses = post_responses or []
        self._post_exceptions = post_exceptions or []
        self._delete_responses = delete_responses or []
        self.calls: list[dict[str, object]] = []
        self.patch_calls: list[dict[str, object]] = []
        self.post_calls: list[dict[str, object]] = []
        self.upsert_calls: list[dict[str, object]] = []
        self.delete_calls: list[dict[str, object]] = []

    def get_rows(self, *, table, query, with_count, use_anon_key=False):
        self.calls.append(
            {
                "table": table,
                "query": query,
                "with_count": with_count,
                "use_anon_key": use_anon_key,
            }
        )
        call_index = len(self.calls) - 1
        count = self._counts[call_index] if call_index < len(self._counts) else None
        return self._responses[call_index], count

    def patch_rows(self, *, table, query, payload, use_anon_key=False):
        self.patch_calls.append(
            {
                "table": table,
                "query": query,
                "payload": payload,
                "use_anon_key": use_anon_key,
            }
        )
        return self._patch_responses[len(self.patch_calls) - 1] if self._patch_responses else []

    def post_rows(self, *, table, payload, use_anon_key=False, prefer="return=representation"):
        call_index = len(self.post_calls)
        self.post_calls.append(
            {
                "table": table,
                "payload": payload,
                "use_anon_key": use_anon_key,
                "prefer": prefer,
            }
        )
        if call_index < len(self._post_exceptions):
            raise self._post_exceptions[call_index]
        if call_index < len(self._post_responses):
            return self._post_responses[call_index]
        return []

    def upsert_row(self, *, table, payload, on_conflict, use_anon_key=False):
        self.upsert_calls.append(
            {
                "table": table,
                "payload": payload,
                "on_conflict": on_conflict,
                "use_anon_key": use_anon_key,
            }
        )
        return []

    def delete_rows(self, *, table, query, use_anon_key=False):
        self.delete_calls.append(
            {
                "table": table,
                "query": query,
                "use_anon_key": use_anon_key,
            }
        )
        return self._delete_responses[len(self.delete_calls) - 1] if self._delete_responses else []


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



def test_get_chat_state_returns_empty_dict_when_row_missing() -> None:
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    user_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    client = _ClientStub(responses=[[]])
    repository = SupabaseProfilesRepository(client=client)

    chat_state = repository.get_chat_state(profile_id=profile_id, user_id=user_id)

    assert chat_state == {}
    assert client.calls[0]["table"] == "chat_state"
    assert client.calls[0]["query"] == {
        "select": "active_task,state",
        "conversation_id": f"eq.{profile_id}",
        "profile_id": f"eq.{profile_id}",
        "user_id": f"eq.{user_id}",
        "limit": 1,
    }


def test_get_chat_state_returns_active_task_and_state_when_present() -> None:
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    user_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    client = _ClientStub(
        responses=[[{"active_task": {"type": "x"}, "state": {"last_query": {"foo": "bar"}}}]]
    )
    repository = SupabaseProfilesRepository(client=client)

    chat_state = repository.get_chat_state(profile_id=profile_id, user_id=user_id)

    assert chat_state == {
        "active_task": {"type": "x"},
        "state": {"last_query": {"foo": "bar"}},
    }


def test_get_chat_state_omits_null_state_value() -> None:
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    user_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    client = _ClientStub(responses=[[{"active_task": {"type": "x"}, "state": None}]])
    repository = SupabaseProfilesRepository(client=client)

    chat_state = repository.get_chat_state(profile_id=profile_id, user_id=user_id)

    assert chat_state == {"active_task": {"type": "x"}}


def test_update_chat_state_uses_upsert() -> None:
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    user_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    client = _ClientStub(responses=[])
    repository = SupabaseProfilesRepository(client=client)

    repository.update_chat_state(
        profile_id=profile_id,
        user_id=user_id,
        chat_state={"active_task": {"type": "x"}, "state": {"step": "confirm"}},
    )

    assert client.upsert_calls == [
        {
            "table": "chat_state",
            "payload": {
                "conversation_id": str(profile_id),
                "user_id": str(user_id),
                "profile_id": str(profile_id),
                "active_task": {"type": "x"},
                "state": {"step": "confirm"},
            },
            "on_conflict": "conversation_id",
            "use_anon_key": False,
        }
    ]
    assert client.calls == []
    assert client.post_calls == []


def test_get_profile_fields_reads_only_selected_columns() -> None:
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    client = _ClientStub(
        responses=[[{"first_name": "Paul", "city": "Bouveret", "birth_date": "2001-07-14"}]]
    )
    repository = SupabaseProfilesRepository(client=client)

    data = repository.get_profile_fields(profile_id=profile_id, fields=["first_name", "city", "birth_date"])

    assert data == {"first_name": "Paul", "city": "Bouveret", "birth_date": "2001-07-14"}
    assert client.calls[0]["query"] == {
        "select": "first_name,city,birth_date",
        "id": f"eq.{profile_id}",
        "limit": 1,
    }


def test_update_profile_fields_patches_single_profile_id_and_returns_updated_fields() -> None:
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    client = _ClientStub(
        responses=[],
        patch_responses=[[{"first_name": "Paul", "city": "Bouveret", "birth_date": "2001-07-14"}]],
    )
    repository = SupabaseProfilesRepository(client=client)

    data = repository.update_profile_fields(
        profile_id=profile_id,
        set_dict={"first_name": "Paul", "city": "Bouveret", "birth_date": "2001-07-14"},
    )

    assert data == {"first_name": "Paul", "birth_date": "2001-07-14"}
    assert client.patch_calls == [
        {
            "table": "profils",
            "query": {"id": f"eq.{profile_id}"},
            "payload": {"first_name": "Paul", "birth_date": "2001-07-14"},
            "use_anon_key": False,
        }
    ]


def test_update_profile_fields_uses_table_update_and_filters_unknown_fields() -> None:
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    class _TableQuery:
        def __init__(self) -> None:
            self.update_payload: dict[str, object] | None = None
            self.eq_filters: list[tuple[str, str]] = []

        def update(self, payload: dict[str, object]):
            self.update_payload = payload
            return self

        def eq(self, field: str, value: str):
            self.eq_filters.append((field, value))
            return self

        def execute(self):
            class _Response:
                data = [{"id": str(profile_id)}]

            return _Response()

    class _TableClientStub:
        def __init__(self) -> None:
            self.table_calls: list[str] = []
            self.query = _TableQuery()

        def table(self, table_name: str):
            self.table_calls.append(table_name)
            return self.query

    client = _TableClientStub()
    repository = SupabaseProfilesRepository(client=client)

    data = repository.update_profile_fields(
        profile_id=profile_id,
        set_dict={"first_name": "Paul", "city": "Bouveret", "birth_date": "2001-07-14"},
    )

    assert data == {"first_name": "Paul", "birth_date": "2001-07-14"}
    assert client.table_calls == ["profils"]
    assert client.query.update_payload == {"first_name": "Paul", "birth_date": "2001-07-14"}
    assert client.query.eq_filters == [("id", str(profile_id))]


def test_hard_reset_profile_deletes_only_filtered_by_profile_id() -> None:
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    user_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    client = _ClientStub(responses=[], patch_responses=[[{"id": str(profile_id)}]])
    repository = SupabaseProfilesRepository(client=client)

    repository.hard_reset_profile(profile_id=profile_id, user_id=user_id)

    assert client.patch_calls == [
        {
            "table": "profils",
            "query": {"id": f"eq.{profile_id}"},
            "payload": {"first_name": None, "last_name": None, "birth_date": None},
            "use_anon_key": False,
        }
    ]
    assert client.upsert_calls == [
        {
            "table": "chat_state",
            "payload": {
                "conversation_id": str(profile_id),
                "user_id": str(user_id),
                "profile_id": str(profile_id),
            },
            "on_conflict": "conversation_id",
            "use_anon_key": False,
        }
    ]
    assert client.delete_calls == [
        {"table": "releves_bancaires", "query": {"profile_id": f"eq.{profile_id}"}, "use_anon_key": False},
        {"table": "merchants", "query": {"profile_id": f"eq.{profile_id}"}, "use_anon_key": False},
        {"table": "profile_categories", "query": {"profile_id": f"eq.{profile_id}"}, "use_anon_key": False},
        {"table": "bank_accounts", "query": {"profile_id": f"eq.{profile_id}"}, "use_anon_key": False},
    ]




def test_list_profile_categories_includes_system_key_in_select() -> None:
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    client = _ClientStub(responses=[[{"id": "1", "system_key": "food"}]])
    repository = SupabaseProfilesRepository(client=client)

    rows = repository.list_profile_categories(profile_id=profile_id)

    assert rows == [{"id": "1", "system_key": "food"}]
    assert client.calls[0]["table"] == "profile_categories"
    assert client.calls[0]["query"] == {
        "select": "id,name,name_norm,system_key,is_system,scope",
        "profile_id": f"eq.{profile_id}",
        "scope": "eq.personal",
        "limit": 200,
    }

def test_list_releves_without_merchant_filters_profile_and_null_merchant() -> None:
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    client = _ClientStub(responses=[[{"id": "1"}]])
    repository = SupabaseProfilesRepository(client=client)

    rows = repository.list_releves_without_merchant(profile_id=profile_id, limit=123)

    assert rows == [{"id": "1"}]
    assert client.calls[0]["table"] == "releves_bancaires"
    assert client.calls[0]["query"] == {
        "select": "id,payee,libelle,created_at,date",
        "profile_id": f"eq.{profile_id}",
        "merchant_entity_id": "is.null",
        "or": "(payee.not.is.null,libelle.not.is.null)",
        "limit": 123,
    }


def test_get_merchant_by_id_filters_profile_scope_and_id() -> None:
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    merchant_id = UUID("11111111-1111-1111-1111-111111111111")
    client = _ClientStub(responses=[[{"id": str(merchant_id), "category": "Courses"}]])
    repository = SupabaseProfilesRepository(client=client)

    row = repository.get_merchant_by_id(profile_id=profile_id, merchant_id=merchant_id)

    assert row == {"id": str(merchant_id), "category": "Courses"}
    assert client.calls[0]["table"] == "merchants"
    assert client.calls[0]["query"] == {
        "select": "id,name,name_norm,aliases,category",
        "profile_id": f"eq.{profile_id}",
        "scope": "eq.personal",
        "id": f"eq.{merchant_id}",
        "limit": 1,
    }


def test_upsert_merchant_by_name_norm_returns_existing_id_without_post() -> None:
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    merchant_id = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
    client = _ClientStub(responses=[[{"id": str(merchant_id)}]], patch_responses=[[]])
    repository = SupabaseProfilesRepository(client=client)

    returned_id = repository.upsert_merchant_by_name_norm(
        profile_id=profile_id,
        name="  Migros  ",
        name_norm="migros",
    )

    assert returned_id == merchant_id
    assert len(client.post_calls) == 0
    assert len(client.patch_calls) == 1
    patch_payload = client.patch_calls[0]["payload"]
    assert patch_payload["last_seen"] != "now()"
    assert "T" in patch_payload["last_seen"]


def test_upsert_merchant_by_name_norm_creates_when_missing() -> None:
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    merchant_id = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
    client = _ClientStub(responses=[[]], post_responses=[[{"id": str(merchant_id)}]])
    repository = SupabaseProfilesRepository(client=client)

    returned_id = repository.upsert_merchant_by_name_norm(
        profile_id=profile_id,
        name="Migros SA",
        name_norm="migros sa",
    )

    assert returned_id == merchant_id
    assert len(client.post_calls) == 1
    assert client.post_calls[0]["table"] == "merchants"
    post_payload = client.post_calls[0]["payload"]
    assert post_payload["last_seen"] != "now()"
    assert "T" in post_payload["last_seen"]


def test_upsert_merchant_by_name_norm_handles_duplicate_key_with_fallback_get() -> None:
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    merchant_id = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
    client = _ClientStub(
        responses=[[], [{"id": str(merchant_id)}]],
        post_exceptions=[RuntimeError("duplicate key value violates unique constraint")],
    )
    repository = SupabaseProfilesRepository(client=client)

    returned_id = repository.upsert_merchant_by_name_norm(
        profile_id=profile_id,
        name="Migros SA",
        name_norm="migros sa",
    )

    assert returned_id == merchant_id
    assert len(client.calls) == 2
    assert len(client.post_calls) == 1


def test_attach_merchant_to_releve_patches_releve_merchant_id() -> None:
    releve_id = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
    merchant_id = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
    client = _ClientStub(responses=[], patch_responses=[[]])
    repository = SupabaseProfilesRepository(client=client)

    repository.attach_merchant_to_releve(releve_id=releve_id, merchant_id=merchant_id)

    assert client.patch_calls == [
        {
            "table": "releves_bancaires",
            "query": {"id": f"eq.{releve_id}"},
            "payload": {"merchant_id": str(merchant_id)},
            "use_anon_key": False,
        }
    ]


def test_append_merchant_alias_patches_when_aliases_is_none() -> None:
    merchant_id = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
    client = _ClientStub(responses=[[{"aliases": None}]], patch_responses=[[]])
    repository = SupabaseProfilesRepository(client=client)

    repository.append_merchant_alias(merchant_id=merchant_id, alias="  COOP-4815 MONTHEY  ")

    assert client.patch_calls == [
        {
            "table": "merchants",
            "query": {"id": f"eq.{merchant_id}"},
            "payload": {"aliases": ["COOP-4815 MONTHEY"]},
            "use_anon_key": False,
        }
    ]


def test_append_merchant_alias_skips_patch_when_alias_already_exists() -> None:
    merchant_id = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
    client = _ClientStub(responses=[[{"aliases": ["COOP-4815 MONTHEY"]}]])
    repository = SupabaseProfilesRepository(client=client)

    repository.append_merchant_alias(merchant_id=merchant_id, alias="COOP-4815 MONTHEY")

    assert client.patch_calls == []


def test_rename_merchant_patches_name_and_name_norm() -> None:
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    merchant_id = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
    client = _ClientStub(responses=[], patch_responses=[[{"id": str(merchant_id)}]])
    repository = SupabaseProfilesRepository(client=client)

    result = repository.rename_merchant(
        profile_id=profile_id,
        merchant_id=merchant_id,
        new_name="  Café   du   Rhône  ",
    )

    assert result == {
        "merchant_id": str(merchant_id),
        "name": "Café du Rhône",
        "name_norm": "cafe du rhone",
    }
    assert client.patch_calls == [
        {
            "table": "merchants",
            "query": {"id": f"eq.{merchant_id}", "profile_id": f"eq.{profile_id}"},
            "payload": {"name": "Café du Rhône", "name_norm": "cafe du rhone"},
            "use_anon_key": False,
        }
    ]


def test_merge_merchants_moves_releves_merges_aliases_and_deletes_source() -> None:
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    source_merchant_id = UUID("11111111-1111-1111-1111-111111111111")
    target_merchant_id = UUID("22222222-2222-2222-2222-222222222222")
    moved_releve_id = UUID("33333333-3333-3333-3333-333333333333")

    client = _ClientStub(
        responses=[
            [
                {
                    "id": str(source_merchant_id),
                    "profile_id": str(profile_id),
                    "scope": "personal",
                    "name": "Migros Monthey",
                    "name_norm": "migros monthey",
                    "aliases": ["MIGROS MONTHEY"],
                    "category": "Alimentation",
                }
            ],
            [
                {
                    "id": str(target_merchant_id),
                    "profile_id": str(profile_id),
                    "scope": "personal",
                    "name": "Migros",
                    "name_norm": "migros",
                    "aliases": ["Migros", "MIGROS MONTHEY"],
                    "category": "Alimentation",
                }
            ],
            [{"id": str(moved_releve_id)}],
        ],
        patch_responses=[
            [],
            [{"id": str(target_merchant_id)}],
        ],
        delete_responses=[[{"id": str(source_merchant_id)}]],
    )
    repository = SupabaseProfilesRepository(client=client)

    result = repository.merge_merchants(
        profile_id=profile_id,
        source_merchant_id=source_merchant_id,
        target_merchant_id=target_merchant_id,
    )

    assert client.calls[0]["table"] == "merchants"
    assert client.calls[0]["query"]["id"] == f"eq.{source_merchant_id}"
    assert client.calls[1]["table"] == "merchants"
    assert client.calls[1]["query"]["id"] == f"eq.{target_merchant_id}"
    assert client.calls[2] == {
        "table": "releves_bancaires",
        "query": {
            "select": "id",
            "profile_id": f"eq.{profile_id}",
            "merchant_id": f"eq.{source_merchant_id}",
            "limit": 5000,
        },
        "with_count": False,
        "use_anon_key": False,
    }

    assert client.patch_calls[0] == {
        "table": "releves_bancaires",
        "query": {"id": f"in.({moved_releve_id})"},
        "payload": {"merchant_id": str(target_merchant_id)},
        "use_anon_key": False,
    }
    assert client.patch_calls[1] == {
        "table": "merchants",
        "query": {"id": f"eq.{target_merchant_id}", "profile_id": f"eq.{profile_id}"},
        "payload": {"aliases": ["Migros", "MIGROS MONTHEY", "Migros Monthey"]},
        "use_anon_key": False,
    }

    assert result == {
        "target_merchant_id": str(target_merchant_id),
        "source_merchant_id": str(source_merchant_id),
        "moved_releves_count": 1,
        "aliases_added_count": 1,
        "target_aliases_count": 3,
    }


def test_upsert_merchant_alias_updates_times_seen_when_alias_exists() -> None:
    entity_id = UUID("11111111-1111-1111-1111-111111111111")
    alias_id = UUID("22222222-2222-2222-2222-222222222222")
    client = _ClientStub(
        responses=[[{"id": str(alias_id), "times_seen": 2}]],
        patch_responses=[[{"id": str(alias_id)}]],
    )
    repository = SupabaseProfilesRepository(client=client)

    repository.upsert_merchant_alias(
        merchant_entity_id=entity_id,
        alias="Coop City",
        alias_norm="coop city",
        source="import",
    )

    assert client.calls == [
        {
            "table": "merchant_aliases",
            "query": {
                "select": "id,times_seen",
                "merchant_entity_id": f"eq.{entity_id}",
                "alias_norm": "eq.coop city",
                "limit": 1,
            },
            "with_count": False,
            "use_anon_key": False,
        }
    ]
    assert len(client.patch_calls) == 1
    assert client.patch_calls[0]["table"] == "merchant_aliases"
    assert client.patch_calls[0]["query"] == {"id": f"eq.{alias_id}"}
    assert client.patch_calls[0]["payload"]["times_seen"] == 3
    assert "last_seen" in client.patch_calls[0]["payload"]


def test_count_map_alias_suggestions_returns_exact_count() -> None:
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    client = _ClientStub(responses=[[]], counts=[7])
    repository = SupabaseProfilesRepository(client=client)

    count = repository.count_map_alias_suggestions(profile_id=profile_id)

    assert count == 7
    assert client.calls == [
        {
            "table": "merchant_suggestions",
            "query": {
                "select": "id",
                "profile_id": f"eq.{profile_id}",
                "action": "eq.map_alias",
                "status": "in.(pending,failed)",
                "limit": 1,
            },
            "with_count": True,
            "use_anon_key": False,
        }
    ]


def test_count_map_alias_suggestions_returns_none_when_count_missing() -> None:
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    client = _ClientStub(responses=[[]], counts=[None])
    repository = SupabaseProfilesRepository(client=client)

    count = repository.count_map_alias_suggestions(profile_id=profile_id)

    assert count is None


def test_update_merchant_suggestion_after_resolve_falls_back_to_minimal_payload() -> None:
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    suggestion_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

    class _ClientPatchFallbackStub(_ClientStub):
        def __init__(self) -> None:
            super().__init__(responses=[])

        def patch_rows(self, *, table, query, payload, use_anon_key=False):
            self.patch_calls.append(
                {
                    "table": table,
                    "query": query,
                    "payload": payload,
                    "use_anon_key": use_anon_key,
                }
            )
            if len(self.patch_calls) == 1:
                raise Exception("schema mismatch")
            return []

    client = _ClientPatchFallbackStub()
    repository = SupabaseProfilesRepository(client=client)

    repository.update_merchant_suggestion_after_resolve(
        profile_id=profile_id,
        suggestion_id=suggestion_id,
        status="failed",
        error="invalid_item",
        llm_model="gpt-test",
        llm_run_id="run_1",
        confidence=0.0,
        rationale="invalid_item",
        target_merchant_entity_id=None,
        suggested_entity_name=None,
        suggested_entity_name_norm=None,
        suggested_category_norm=None,
        suggested_category_label=None,
    )

    assert len(client.patch_calls) == 2
    assert client.patch_calls[1]["payload"] == {"status": "failed", "error": "invalid_item"}


def test_create_map_alias_suggestions_uses_insert_and_deduplicates_batch() -> None:
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    client = _ClientStub(responses=[])
    repository = SupabaseProfilesRepository(client=client)

    inserted = repository.create_map_alias_suggestions(
        profile_id=profile_id,
        rows=[
            {
                "status": "pending",
                "observed_alias": "Unknown Shop",
                "observed_alias_norm": "unknown shop",
            },
            {
                "status": "failed",
                "observed_alias": "UNKNOWN SHOP",
                "observed_alias_norm": "unknown shop",
            },
        ],
    )

    assert inserted == 1
    assert client.post_calls == [
        {
            "table": "merchant_suggestions",
            "payload": {
                "profile_id": str(profile_id),
                "action": "map_alias",
                "status": "pending",
                "observed_alias": "Unknown Shop",
                "observed_alias_norm": "unknown shop",
                "suggested_entity_name": None,
                "confidence": None,
                "rationale": None,
            },
            "use_anon_key": False,
            "prefer": "return=representation",
        }
    ]
    assert client.upsert_calls == []


def test_create_map_alias_suggestions_ignores_duplicate_key_errors() -> None:
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    client = _ClientStub(
        responses=[],
        post_exceptions=[RuntimeError("duplicate key value violates unique constraint")],
    )
    repository = SupabaseProfilesRepository(client=client)

    inserted = repository.create_map_alias_suggestions(
        profile_id=profile_id,
        rows=[
            {
                "status": "pending",
                "observed_alias": "Unknown Shop",
                "observed_alias_norm": "unknown shop",
            }
        ],
    )

    assert inserted == 0
    assert len(client.post_calls) == 1


def test_create_map_alias_suggestions_never_upserts_and_ignores_pending_duplicates() -> None:
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    class _DuplicatePendingError(Exception):
        code = "23505"

    client = _ClientStub(
        responses=[],
        post_exceptions=[_DuplicatePendingError("duplicate key value violates unique constraint")],
    )
    repository = SupabaseProfilesRepository(client=client)

    inserted = repository.create_map_alias_suggestions(
        profile_id=profile_id,
        rows=[
            {
                "status": "pending",
                "observed_alias": "Resolved Shop",
                "observed_alias_norm": "resolved shop",
            }
        ],
    )

    assert client.upsert_calls == []
    assert len(client.post_calls) == 1
    assert inserted == 0
