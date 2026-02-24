"""Deterministic routing for conversational loops."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Protocol

from agent.loops.registry import LoopRegistry
from agent.loops.types import LoopContext, LoopReply


_SWITCH_KEYWORDS = {
    "profil": "onboarding.profile_collect",
    "compte": "onboarding.bank_accounts_collect",
    "import": "onboarding.import_select_account",
    "catégories": "onboarding.categories_intro",
    "categories": "onboarding.categories_intro",
    "rapport": "onboarding.report",
    "partage": "household_link.setup",
    "foyer": "household_link.setup",
}


class LLMJudge(Protocol):
    def __call__(self, *, message: str, current_loop_id: str | None, candidate_loop_ids: list[str]) -> str | None:
        """Return loop id to switch to when ambiguity remains."""


def _detect_switch_target(message: str, candidate_loop_ids: list[str]) -> str | None:
    lowered = message.lower()
    matches: list[str] = []
    for keyword, loop_id in _SWITCH_KEYWORDS.items():
        if keyword in lowered and loop_id in candidate_loop_ids:
            matches.append(loop_id)
    unique = sorted(set(matches))
    if len(unique) == 1:
        return unique[0]
    return None


def route_message(
    message: str,
    *,
    current_loop: LoopContext | None,
    global_state: dict[str, Any],
    services: Any,
    profile_id: Any,
    user_id: Any,
    llm_judge: LLMJudge | None,
    registry: LoopRegistry,
) -> LoopReply:
    """Route message with deterministic-first loop policy."""

    if current_loop is not None:
        active_loop = registry.get(current_loop.loop_id)
        if active_loop is not None:
            reply = active_loop.handle(
                message,
                current_loop,
                services=services,
                profile_id=profile_id,
                user_id=user_id,
            )
            if reply.handled:
                return reply
            if current_loop.blocking:
                return LoopReply(
                    reply="On continue d'abord cette étape. Réponds à la question en cours.",
                    next_loop=current_loop,
                    updates={},
                    handled=True,
                )

    enterable = [loop for loop in registry.list_loops() if loop.can_enter(global_state, services, profile_id, user_id)]

    if current_loop is None and enterable:
        chosen = sorted(enterable, key=lambda item: item.id)[0]
        next_ctx = LoopContext(loop_id=chosen.id, step="start", data={}, blocking=chosen.blocking)
        loop_reply = chosen.handle(
            message,
            next_ctx,
            services=services,
            profile_id=profile_id,
            user_id=user_id,
        )
        if loop_reply.next_loop is None:
            loop_reply.next_loop = next_ctx
        return loop_reply

    candidate_ids = [loop.id for loop in registry.list_loops()]
    switch_target = _detect_switch_target(message, candidate_ids)
    if switch_target is None and llm_judge is not None:
        switch_target = llm_judge(
            message=message,
            current_loop_id=current_loop.loop_id if current_loop else None,
            candidate_loop_ids=candidate_ids,
        )

    if switch_target is not None:
        loop = registry.get(switch_target)
        if loop is not None:
            new_ctx = LoopContext(loop_id=loop.id, step="start", data={}, blocking=loop.blocking)
            routed = loop.handle(message, new_ctx, services=services, profile_id=profile_id, user_id=user_id)
            if routed.next_loop is None:
                routed.next_loop = new_ctx
            return routed

    return LoopReply(reply="", next_loop=current_loop, updates={}, handled=False)


def serialize_loop_context(ctx: LoopContext | None) -> dict[str, Any] | None:
    if ctx is None:
        return None
    return asdict(ctx)


def parse_loop_context(value: Any) -> LoopContext | None:
    if not isinstance(value, dict):
        return None
    loop_id = value.get("loop_id")
    step = value.get("step")
    data = value.get("data")
    blocking = value.get("blocking")
    if not isinstance(loop_id, str) or not isinstance(step, str):
        return None
    if not isinstance(data, dict):
        data = {}
    if not isinstance(blocking, bool):
        blocking = True
    return LoopContext(loop_id=loop_id, step=step, data=dict(data), blocking=blocking)
