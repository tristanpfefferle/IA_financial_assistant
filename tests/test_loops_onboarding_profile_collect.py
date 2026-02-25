from __future__ import annotations

from uuid import uuid4

from agent.loops.onboarding_profile import OnboardingProfileCollectLoop
from agent.loops.types import LoopContext


class _ProfilesRepoStub:
    def __init__(self, profile_fields: dict[str, str | None]) -> None:
        self.profile_fields = dict(profile_fields)
        self.update_calls: list[dict[str, str]] = []

    def get_profile_fields(self, *, profile_id, fields):
        _ = profile_id
        return {field: self.profile_fields.get(field) for field in fields}

    def update_profile_fields(self, *, profile_id, set_dict, user_id=None):
        _ = profile_id
        _ = user_id
        self.update_calls.append(dict(set_dict))
        self.profile_fields.update(set_dict)
        return dict(set_dict)


def test_collect_waits_for_structured_profile_card() -> None:
    repo = _ProfilesRepoStub({"first_name": None, "last_name": None, "birth_date": None})
    loop = OnboardingProfileCollectLoop()

    reply = loop.handle(
        "Paul",
        LoopContext(loop_id=loop.id, step="active", data={}, blocking=True),
        services={"profiles_repository": repo, "global_state": {}},
        profile_id=uuid4(),
        user_id=uuid4(),
    )

    assert reply.handled is True
    assert repo.update_calls == []
    assert "carte profil" in reply.reply
    assert isinstance(reply.updates.get("global_state"), dict)
    assert reply.updates["global_state"]["onboarding_substep"] == "profile_collect"


def test_collect_completed_profile_moves_to_profile_confirm_with_recap() -> None:
    repo = _ProfilesRepoStub({"first_name": "Paul", "last_name": "Murt", "birth_date": "1990-01-01"})
    loop = OnboardingProfileCollectLoop()

    reply = loop.handle(
        "n'importe quoi",
        LoopContext(loop_id=loop.id, step="active", data={}, blocking=True),
        services={"profiles_repository": repo, "global_state": {}},
        profile_id=uuid4(),
        user_id=uuid4(),
    )

    assert reply.handled is True
    assert repo.update_calls == []
    assert isinstance(reply.updates.get("global_state"), dict)
    assert reply.updates["global_state"]["onboarding_substep"] == "profile_confirm"
    assert "Récapitulatif de ton profil" in reply.reply


def test_expected_prompt_for_birth_date_is_form_prompt() -> None:
    repo = _ProfilesRepoStub({"first_name": "Paul", "last_name": "Murt", "birth_date": None})
    loop = OnboardingProfileCollectLoop()

    prompt = loop.expected_prompt_for_help(services={"profiles_repository": repo, "state": {}}, profile_id=uuid4())

    assert "carte profil" in prompt
