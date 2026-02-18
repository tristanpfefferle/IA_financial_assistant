from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import agent.loop as loop_module
from agent.loop import AgentLoop, AgentReply


PROFILE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


class _Router:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call(
        self,
        tool_name: str,
        payload: dict[str, object],
        *,
        profile_id: UUID | None = None,
    ) -> dict[str, object]:
        assert profile_id == PROFILE_ID
        self.calls.append((tool_name, dict(payload)))
        if tool_name == "finance_releves_sum":
            return {"ok": True, "total": 10.0}
        raise AssertionError(f"unexpected tool call: {tool_name}")


def test_followup_explicit_period_reuses_last_tool_filters_without_clarification(monkeypatch) -> None:
    def _parse_intent(message: str):
        if message == "Total dépenses Loisir en décembre 2025":
            return {
                "type": "tool_call",
                "tool_name": "finance_releves_sum",
                "payload": {
                    "direction": "DEBIT_ONLY",
                    "categorie": "Loisir",
                    "date_range": {
                        "start_date": "2025-12-01",
                        "end_date": "2025-12-31",
                    },
                },
            }
        raise AssertionError(f"Unexpected parse_intent call for: {message}")

    monkeypatch.setattr(loop_module, "parse_intent", _parse_intent)

    router = _Router()
    loop = AgentLoop(tool_router=router)

    memory_state: dict[str, object] = {"known_categories": ["Loisir"]}

    first_reply = loop.handle_user_message(
        "Total dépenses Loisir en décembre 2025",
        profile_id=PROFILE_ID,
        memory=memory_state,
    )

    assert first_reply.plan is not None
    assert first_reply.plan["tool_name"] == "finance_releves_sum"
    assert first_reply.memory_update is not None
    memory_state = {**memory_state, **first_reply.memory_update}

    second_reply = loop.handle_user_message(
        "et en janvier 2026",
        profile_id=PROFILE_ID,
        memory=memory_state,
        debug=True,
    )

    assert second_reply.plan is not None
    assert second_reply.plan["tool_name"] == "finance_releves_sum"
    assert second_reply.plan["payload"] == {
        "direction": "DEBIT_ONLY",
        "categorie": "Loisir",
        "date_range": {
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
        },
    }
    assert second_reply.tool_result is not None
    assert second_reply.tool_result.get("type") != "clarification"
    assert len(router.calls) == 2


def test_direction_clarification_does_not_overwrite_category() -> None:
    router = _Router()
    loop = AgentLoop(tool_router=router)

    reply = loop.handle_user_message(
        "Dépenses",
        profile_id=PROFILE_ID,
        active_task={
            "type": "clarification_pending",
            "context": {
                "clarification_type": "direction_choice",
                "period_payload": {
                    "date_range": {
                        "start_date": "2026-01-01",
                        "end_date": "2026-01-31",
                    }
                },
                "base_payload": {"categorie": "Loisir"},
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    assert reply.plan is not None
    assert reply.plan["tool_name"] == "finance_releves_sum"
    payload = reply.plan["payload"]
    assert payload["direction"] == "DEBIT_ONLY"
    assert payload.get("categorie") == "Loisir"
    assert payload.get("categorie") != "Dépenses"


def test_direction_clarification_legacy_type_missing_direction_keeps_category() -> None:
    router = _Router()
    loop = AgentLoop(tool_router=router)

    reply = loop.handle_user_message(
        "Dépenses",
        profile_id=PROFILE_ID,
        active_task={
            "type": "clarification_pending",
            "context": {
                "clarification_type": "missing_direction",
                "period_payload": {
                    "date_range": {
                        "start_date": "2026-01-01",
                        "end_date": "2026-01-31",
                    }
                },
                "base_payload": {"categorie": "Loisir"},
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    assert reply.plan is not None
    assert reply.plan["tool_name"] == "finance_releves_sum"
    payload = reply.plan["payload"]
    assert payload["direction"] == "DEBIT_ONLY"
    assert payload.get("categorie") == "Loisir"
    assert payload.get("categorie") != "Dépenses"


def test_stale_clarification_pending_is_cleared_on_new_request(monkeypatch) -> None:
    def _parse_intent(message: str):
        assert message == "Total dépenses Loisir en mars 2026"
        return {
            "type": "tool_call",
            "tool_name": "finance_releves_sum",
            "payload": {
                "direction": "DEBIT_ONLY",
                "categorie": "Loisir",
                "date_range": {
                    "start_date": "2026-03-01",
                    "end_date": "2026-03-31",
                },
            },
        }

    monkeypatch.setattr(loop_module, "parse_intent", _parse_intent)

    router = _Router()
    loop = AgentLoop(tool_router=router)
    stale_created_at = datetime.now(timezone.utc) - timedelta(
        seconds=loop_module._ACTIVE_TASK_TTL_SECONDS + 1
    )

    reply = loop.handle_user_message(
        "Total dépenses Loisir en mars 2026",
        profile_id=PROFILE_ID,
        active_task={
            "type": "clarification_pending",
            "context": {
                "clarification_type": "direction_choice",
                "period_payload": {
                    "date_range": {
                        "start_date": "2025-01-01",
                        "end_date": "2025-01-31",
                    }
                },
            },
            "created_at": stale_created_at.isoformat(),
        },
        memory={"known_categories": ["Loisir"]},
    )

    assert reply.should_update_active_task is True
    assert reply.active_task is None
    assert reply.plan is not None
    assert reply.plan["tool_name"] == "finance_releves_sum"
    assert "year" not in reply.plan["payload"]
    assert "date_range" in reply.plan["payload"]


def test_non_stale_clarification_pending_is_ignored_for_full_new_request(monkeypatch) -> None:
    def _parse_intent(message: str):
        assert message == "Total dépenses Loisir en mars 2026"
        return {
            "type": "tool_call",
            "tool_name": "finance_releves_sum",
            "payload": {
                "direction": "DEBIT_ONLY",
                "categorie": "Loisir",
                "date_range": {
                    "start_date": "2026-03-01",
                    "end_date": "2026-03-31",
                },
            },
        }

    monkeypatch.setattr(loop_module, "parse_intent", _parse_intent)

    router = _Router()
    loop = AgentLoop(tool_router=router)

    reply = loop.handle_user_message(
        "Total dépenses Loisir en mars 2026",
        profile_id=PROFILE_ID,
        active_task={
            "type": "clarification_pending",
            "context": {
                "clarification_type": "direction_choice",
                "period_payload": {
                    "date_range": {
                        "start_date": "2025-01-01",
                        "end_date": "2025-01-31",
                    }
                },
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        memory={"known_categories": ["Loisir"]},
    )

    assert reply.should_update_active_task is True
    assert reply.active_task is None
    assert reply.plan is not None
    assert reply.plan["tool_name"] == "finance_releves_sum"
    assert "year" not in reply.plan["payload"]
    assert "date_range" in reply.plan["payload"]


def test_clarification_pending_still_handles_short_direction_answer() -> None:
    router = _Router()
    loop = AgentLoop(tool_router=router)

    reply = loop.handle_user_message(
        "Dépenses",
        profile_id=PROFILE_ID,
        active_task={
            "type": "clarification_pending",
            "context": {
                "clarification_type": "direction_choice",
                "period_payload": {
                    "date_range": {
                        "start_date": "2026-01-01",
                        "end_date": "2026-01-31",
                    }
                },
                "base_payload": {"categorie": "Loisir"},
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    assert reply.plan is not None
    assert reply.plan["tool_name"] == "finance_releves_sum"
    assert reply.plan["payload"]["direction"] == "DEBIT_ONLY"
    assert reply.plan["payload"]["categorie"] == "Loisir"
    assert reply.active_task is None
    assert reply.should_update_active_task is True


def test_forced_clear_active_task_even_when_routing_returns_reply(monkeypatch) -> None:
    router = _Router()
    loop = AgentLoop(tool_router=router)

    def _route_message(
        self: AgentLoop,
        message: str,
        *,
        profile_id: UUID | None = None,
        active_task: dict[str, object] | None = None,
    ) -> AgentReply:
        del self, message, profile_id, active_task
        return AgentReply(reply="hi")

    monkeypatch.setattr(AgentLoop, "_route_message", _route_message)

    reply = loop.handle_user_message(
        "Total dépenses Loisir en mars 2026",
        profile_id=PROFILE_ID,
        active_task={
            "type": "clarification_pending",
            "context": {
                "clarification_type": "direction_choice",
                "period_payload": {
                    "date_range": {
                        "start_date": "2025-01-01",
                        "end_date": "2025-01-31",
                    }
                },
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        memory={"known_categories": ["Loisir"]},
    )

    assert reply.reply == "hi"
    assert reply.should_update_active_task is True
    assert reply.active_task is None


def test_tool_call_payload_omits_none_filters_before_router_call(monkeypatch) -> None:
    def _parse_intent(message: str):
        assert message == "Dépenses pizza"
        return {
            "type": "tool_call",
            "tool_name": "finance_releves_sum",
            "payload": {
                "direction": "DEBIT_ONLY",
                "merchant": "pizza",
                "categorie": None,
            },
        }

    monkeypatch.setattr(loop_module, "parse_intent", _parse_intent)

    router = _Router()
    loop = AgentLoop(tool_router=router)

    reply = loop.handle_user_message(
        "Dépenses pizza",
        profile_id=PROFILE_ID,
    )

    assert reply.plan is not None
    assert router.calls
    _, payload = router.calls[-1]
    assert payload.get("merchant") == "pizza"
    assert "categorie" not in payload
