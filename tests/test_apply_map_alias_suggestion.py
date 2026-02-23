from __future__ import annotations

from uuid import UUID

from backend.services.merchant_suggestions.apply_map_alias import apply_map_alias_suggestion


PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SUGGESTION_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
TARGET_ENTITY_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
CREATED_ENTITY_ID = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")


class _RepoStub:
    def __init__(self, suggestion: dict[str, object], existing_entity: dict[str, object] | None = None) -> None:
        self.suggestion = suggestion
        self.existing_entity = existing_entity
        self.status_updates: list[tuple[str, str | None]] = []
        self.alias_upserts: list[dict[str, object]] = []
        self.create_calls: list[dict[str, object]] = []

    def get_merchant_suggestion_by_id(self, *, profile_id: UUID, suggestion_id: UUID):
        assert profile_id == PROFILE_ID
        assert suggestion_id == SUGGESTION_ID
        return self.suggestion

    def update_merchant_suggestion_status(
        self,
        *,
        profile_id: UUID,
        suggestion_id: UUID,
        status: str,
        error: str | None = None,
    ) -> None:
        assert profile_id == PROFILE_ID
        assert suggestion_id == SUGGESTION_ID
        self.status_updates.append((status, error))

    def get_merchant_entity_by_canonical_name_norm(self, *, country: str, canonical_name_norm: str):
        assert country == "CH"
        assert canonical_name_norm
        return self.existing_entity

    def create_merchant_entity(self, **kwargs):
        self.create_calls.append(kwargs)
        return CREATED_ENTITY_ID

    def upsert_merchant_alias(self, **kwargs) -> None:
        self.alias_upserts.append(kwargs)


def test_apply_uses_target_entity_id() -> None:
    repo = _RepoStub(
        suggestion={
            "id": str(SUGGESTION_ID),
            "action": "map_alias",
            "target_merchant_entity_id": str(TARGET_ENTITY_ID),
            "observed_alias": "Coop city",
            "observed_alias_norm": "coop city",
        }
    )

    result = apply_map_alias_suggestion(
        profile_id=PROFILE_ID,
        suggestion_id=SUGGESTION_ID,
        repositories=repo,
    )

    assert result["status"] == "applied"
    assert repo.alias_upserts == [
        {
            "merchant_entity_id": TARGET_ENTITY_ID,
            "alias": "Coop city",
            "alias_norm": "coop city",
            "source": "suggestion_apply",
        }
    ]
    assert repo.status_updates[-1] == ("applied", None)


def test_apply_creates_entity_when_missing() -> None:
    repo = _RepoStub(
        suggestion={
            "id": str(SUGGESTION_ID),
            "action": "map_alias",
            "observed_alias": "Migros MMM",
            "observed_alias_norm": "migros mmm",
            "suggested_entity_name": "Migros MMM",
            "suggested_entity_name_norm": "migros mmm",
        },
        existing_entity=None,
    )

    result = apply_map_alias_suggestion(
        profile_id=PROFILE_ID,
        suggestion_id=SUGGESTION_ID,
        repositories=repo,
    )

    assert result["status"] == "applied"
    assert repo.create_calls
    assert repo.alias_upserts[0]["merchant_entity_id"] == CREATED_ENTITY_ID
    assert repo.status_updates[-1] == ("applied", None)


def test_apply_marks_failed_on_missing_observed_alias_norm() -> None:
    repo = _RepoStub(
        suggestion={
            "id": str(SUGGESTION_ID),
            "action": "map_alias",
            "target_merchant_entity_id": str(TARGET_ENTITY_ID),
            "observed_alias": "Migros MMM",
            "observed_alias_norm": "",
        }
    )

    result = apply_map_alias_suggestion(
        profile_id=PROFILE_ID,
        suggestion_id=SUGGESTION_ID,
        repositories=repo,
    )

    assert result["status"] == "failed"
    assert repo.alias_upserts == []
    assert repo.status_updates[-1][0] == "failed"
    assert "missing observed_alias_norm" in str(repo.status_updates[-1][1])
