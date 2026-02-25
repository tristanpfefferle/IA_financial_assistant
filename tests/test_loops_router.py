from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.loops.registry import LoopRegistry
from agent.loops.router import route_message
from agent.loops.types import LoopContext, LoopReply


@dataclass
class _Loop:
    id: str
    blocking: bool = True

    def can_enter(self, global_state: dict[str, Any], services: Any, profile_id: Any, user_id: Any) -> bool:
        return bool(global_state.get("enter"))

    def handle(self, message: str, ctx: LoopContext, *, services: Any, profile_id: Any, user_id: Any) -> LoopReply:
        if message.strip().lower() == "ok":
            return LoopReply(reply="done", next_loop=None, updates={}, handled=True)
        return LoopReply(reply="", next_loop=ctx, updates={}, handled=False)


@dataclass
class _OptionalLoop(_Loop):
    blocking: bool = False


def test_loop_blocking_reorients_on_digression() -> None:
    registry = LoopRegistry()
    registry.register(_Loop(id="onboarding.profile_confirm", blocking=True))

    result = route_message(
        "parlons météo",
        current_loop=LoopContext(loop_id="onboarding.profile_confirm", step="start", data={}, blocking=True),
        global_state={},
        services=None,
        profile_id=None,
        user_id=None,
        llm_judge=None,
        registry=registry,
    )

    assert result.handled is True
    assert result.reply == "Confirme ton profil (oui/non)."
    assert result.next_loop is not None
    assert result.next_loop.loop_id == "onboarding.profile_confirm"


def test_loop_switch_non_blocking_allowed() -> None:
    registry = LoopRegistry()
    registry.register(_OptionalLoop(id="household_link.setup", blocking=False))
    registry.register(_OptionalLoop(id="onboarding.bank_accounts_collect", blocking=False))

    result = route_message(
        "je veux parler de compte",
        current_loop=LoopContext(loop_id="household_link.setup", step="start", data={}, blocking=False),
        global_state={},
        services=None,
        profile_id=None,
        user_id=None,
        llm_judge=None,
        registry=registry,
    )

    assert result.next_loop is not None
    assert result.next_loop.loop_id == "onboarding.bank_accounts_collect"
