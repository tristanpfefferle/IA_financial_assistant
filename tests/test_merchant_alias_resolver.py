from __future__ import annotations

from uuid import UUID

from agent import merchant_alias_resolver as resolver


PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SUGGESTION_ID = UUID("11111111-1111-1111-1111-111111111111")
ENTITY_ID = UUID("22222222-2222-2222-2222-222222222222")
CATEGORY_ID = UUID("33333333-3333-3333-3333-333333333333")


class _RepoStub:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.updates: list[dict] = []

    def list_map_alias_suggestions(self, *, profile_id: UUID, limit: int):
        self.events.append("list_map_alias_suggestions")
        assert profile_id == PROFILE_ID
        assert limit == 10
        return [
            {
                "id": str(SUGGESTION_ID),
                "observed_alias": "COOP CITY",
                "observed_alias_norm": "coop city",
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]

    def ensure_system_categories(self, *, profile_id: UUID, categories: list[dict[str, str]]):
        self.events.append("ensure_system_categories")
        assert profile_id == PROFILE_ID
        assert categories
        return {"created_count": 0, "system_total_count": len(categories)}

    def list_profile_categories(self, *, profile_id: UUID):
        self.events.append("list_profile_categories")
        assert profile_id == PROFILE_ID
        return [
            {
                "id": str(CATEGORY_ID),
                "system_key": "food",
                "name_norm": "alimentation",
            }
        ]

    def create_merchant_entity(self, **kwargs):
        self.events.append("create_merchant_entity")
        assert kwargs["canonical_name"] == "Coop City"
        return {"id": str(ENTITY_ID)}

    def upsert_merchant_alias(self, **kwargs):
        self.events.append("upsert_merchant_alias")
        assert kwargs["merchant_entity_id"] == ENTITY_ID

    def upsert_profile_merchant_override(self, **kwargs):
        self.events.append("upsert_profile_merchant_override")
        assert kwargs["category_id"] == CATEGORY_ID

    def apply_entity_to_profile_transactions(self, **kwargs):
        self.events.append("apply_entity_to_profile_transactions")
        assert kwargs["category_id"] == CATEGORY_ID
        assert kwargs["observed_alias"] == "COOP CITY"
        return 2

    def update_merchant_suggestion_after_resolve(self, **kwargs):
        self.events.append(f"update_{kwargs['status']}")
        self.updates.append(kwargs)


def test_resolver_create_entity_path(monkeypatch) -> None:
    repo = _RepoStub()

    monkeypatch.setattr(
        resolver,
        "_call_llm_json",
        lambda _prompt: (
            {
                "resolutions": [
                    {
                        "suggestion_id": str(SUGGESTION_ID),
                        "action": "create_entity",
                        "merchant_entity_id": None,
                        "canonical_name": "Coop City",
                        "canonical_name_norm": "coop city",
                        "country": "CH",
                        "suggested_category_norm": "food",
                        "suggested_category_label": "Alimentation",
                        "confidence": 0.92,
                        "rationale": "grocery chain",
                    }
                ]
            },
            "run_1",
            {"total_tokens": 10},
        ),
    )

    stats = resolver.resolve_pending_map_alias(profile_id=PROFILE_ID, profiles_repository=repo, limit=10)

    assert stats["applied"] == 1
    assert stats["created_entities"] == 1
    assert stats["linked_aliases"] == 1
    assert stats["updated_transactions"] == 2
    assert stats["failed"] == 0
    assert repo.events.index("create_merchant_entity") < repo.events.index("upsert_merchant_alias")
    assert repo.events.index("upsert_merchant_alias") < repo.events.index("upsert_profile_merchant_override")
    assert repo.events[-1] == "update_applied"


def test_resolver_link_existing_path_skips_create(monkeypatch) -> None:
    repo = _RepoStub()

    monkeypatch.setattr(repo, "create_merchant_entity", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("should not create")))
    monkeypatch.setattr(
        resolver,
        "_call_llm_json",
        lambda _prompt: (
            {
                "resolutions": [
                    {
                        "suggestion_id": str(SUGGESTION_ID),
                        "action": "link_existing",
                        "merchant_entity_id": str(ENTITY_ID),
                        "canonical_name": None,
                        "canonical_name_norm": None,
                        "country": "CH",
                        "suggested_category_norm": "food",
                        "suggested_category_label": "Alimentation",
                        "confidence": 0.9,
                        "rationale": "already known",
                    }
                ]
            },
            "run_2",
            {},
        ),
    )

    stats = resolver.resolve_pending_map_alias(profile_id=PROFILE_ID, profiles_repository=repo, limit=10)

    assert stats["applied"] == 1
    assert stats["created_entities"] == 0


def test_resolver_marks_invalid_item_failed(monkeypatch) -> None:
    repo = _RepoStub()

    monkeypatch.setattr(
        resolver,
        "_call_llm_json",
        lambda _prompt: (
            {
                "resolutions": [
                    {
                        "suggestion_id": str(SUGGESTION_ID),
                        "action": "create_entity",
                        "merchant_entity_id": None,
                        "canonical_name": None,
                        "canonical_name_norm": None,
                        "country": "CH",
                        "suggested_category_norm": "food",
                        "suggested_category_label": "Alimentation",
                        "confidence": 0.9,
                        "rationale": "invalid",
                    }
                ]
            },
            "run_3",
            {},
        ),
    )

    stats = resolver.resolve_pending_map_alias(profile_id=PROFILE_ID, profiles_repository=repo, limit=10)

    assert stats["failed"] == 1
    assert stats["applied"] == 0
    assert repo.updates[-1]["status"] == "failed"



def test_batching_limit_25_calls_llm_twice(monkeypatch) -> None:
    class _BatchRepo(_RepoStub):
        def list_map_alias_suggestions(self, *, profile_id: UUID, limit: int):
            assert profile_id == PROFILE_ID
            assert limit == 25
            return [
                {
                    "id": f"00000000-0000-0000-0000-{i:012d}",
                    "observed_alias": f"ALIAS {i}",
                    "observed_alias_norm": f"alias {i}",
                }
                for i in range(1, 26)
            ]

        def apply_entity_to_profile_transactions(self, **kwargs):
            return 0

    repo = _BatchRepo()
    calls: list[int] = []

    def _fake_call(prompt: str):
        payload = __import__("json").loads(prompt.split("Suggestions à résoudre: ", 1)[1])
        calls.append(len(payload))
        return {"resolutions": []}, "run", {}

    monkeypatch.setattr(resolver, "_call_llm_json", _fake_call)

    stats = resolver.resolve_pending_map_alias(profile_id=PROFILE_ID, profiles_repository=repo, limit=25)

    assert calls == [20, 5]
    assert stats["processed"] == 25
    assert stats["failed"] == 25


def test_truncation_observed_alias_compact_in_prompt(monkeypatch) -> None:
    class _LongAliasRepo(_RepoStub):
        def list_map_alias_suggestions(self, *, profile_id: UUID, limit: int):
            return [
                {
                    "id": str(SUGGESTION_ID),
                    "observed_alias": "Paiement UBS TWINT Motif du paiement " + ("X" * 220),
                    "observed_alias_norm": "paiement ubs twint motif du paiement " + ("x" * 220),
                }
            ]

        def apply_entity_to_profile_transactions(self, **kwargs):
            return 0

    repo = _LongAliasRepo()
    captured_prompt = {"value": ""}

    def _fake_call(prompt: str):
        captured_prompt["value"] = prompt
        return {"resolutions": []}, "run", {}

    monkeypatch.setattr(resolver, "_call_llm_json", _fake_call)

    resolver.resolve_pending_map_alias(profile_id=PROFILE_ID, profiles_repository=repo, limit=1)

    payload = __import__("json").loads(captured_prompt["value"].split("Suggestions à résoudre: ", 1)[1])
    compact = payload[0]["observed_alias_compact"]
    assert len(compact) <= 140
    assert "Paiement UBS TWINT" not in compact
    assert "Motif du paiement" not in compact


def test_fallback_response_format(monkeypatch) -> None:
    class _Response:
        id = "run_fallback"

        class usage:
            prompt_tokens = 10
            completion_tokens = 4
            total_tokens = 14

        class _Choice:
            class _Message:
                content = '{"resolutions": []}'

            message = _Message()

        choices = [_Choice()]

    class _Client:
        def __init__(self, *args, **kwargs):
            self.calls = []
            self.count = 0

        class _Chat:
            def __init__(self, parent):
                self.completions = parent

        @property
        def chat(self):
            return self._Chat(self)

        def create(self, **kwargs):
            self.count += 1
            self.calls.append(kwargs)
            if self.count == 1:
                raise Exception("invalid_request_error: response_format not supported with this model")
            return _Response()

    client = _Client()
    monkeypatch.setattr(resolver._config, "openai_api_key", lambda: "test")
    monkeypatch.setattr(resolver._config, "llm_model", lambda: "gpt-test")
    monkeypatch.setitem(__import__("sys").modules, "openai", type("M", (), {"OpenAI": lambda **kwargs: client}))

    payload, llm_run_id, usage = resolver._call_llm_json("prompt")

    assert payload == {"resolutions": []}
    assert llm_run_id == "run_fallback"
    assert usage["total_tokens"] == 14
    assert len(client.calls) == 2
    assert "response_format" in client.calls[0]
    assert "response_format" not in client.calls[1]


def test_resolver_continues_when_merchant_suggestion_update_fails(monkeypatch) -> None:
    class _RepoWithUpdateFailure(_RepoStub):
        def update_merchant_suggestion_after_resolve(self, **kwargs):
            raise Exception("schema mismatch")

    repo = _RepoWithUpdateFailure()

    monkeypatch.setattr(
        resolver,
        "_call_llm_json",
        lambda _prompt: (
            {
                "resolutions": [
                    {
                        "suggestion_id": str(SUGGESTION_ID),
                        "action": "link_existing",
                        "merchant_entity_id": str(ENTITY_ID),
                        "canonical_name": None,
                        "canonical_name_norm": None,
                        "country": "CH",
                        "suggested_category_norm": "food",
                        "suggested_category_label": "Alimentation",
                        "confidence": 0.9,
                        "rationale": "already known",
                    }
                ]
            },
            "run_update_fail",
            {},
        ),
    )

    stats = resolver.resolve_pending_map_alias(profile_id=PROFILE_ID, profiles_repository=repo, limit=10)

    assert stats["processed"] == 1
    assert "merchant_suggestion_update_failed" in stats["warnings"]
