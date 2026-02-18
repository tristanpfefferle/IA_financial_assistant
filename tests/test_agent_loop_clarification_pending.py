from __future__ import annotations

from uuid import UUID

import agent.loop as loop_module
from agent.loop import AgentLoop


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


def test_clarification_pending_keeps_explicit_period_context(monkeypatch) -> None:
    def _parse_intent(message: str):
        if message == "Dépenses en Loisir en décembre 2025":
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
        if message == "Et en janvier 2026 ?":
            return {
                "type": "clarification",
                "message": "Tu veux les dépenses, revenus ou le solde ?",
                "clarification_type": "missing_direction",
            }
        return {"type": "noop"}

    monkeypatch.setattr(loop_module, "parse_intent", _parse_intent)

    router = _Router()
    loop = AgentLoop(tool_router=router)

    memory_state: dict[str, object] = {"known_categories": ["Loisir"]}

    first_reply = loop.handle_user_message(
        "Dépenses en Loisir en décembre 2025",
        profile_id=PROFILE_ID,
        memory=memory_state,
    )

    assert first_reply.plan is not None
    assert first_reply.plan["tool_name"] == "finance_releves_sum"
    assert first_reply.memory_update is not None
    memory_state = {**memory_state, **first_reply.memory_update}

    second_reply = loop.handle_user_message(
        "Et en janvier 2026 ?",
        profile_id=PROFILE_ID,
        memory=memory_state,
        debug=True,
    )

    assert second_reply.tool_result is not None
    assert second_reply.plan is not None
    assert second_reply.plan["tool_name"] == "finance_releves_sum"
    assert second_reply.plan["meta"]["debug_source"] == "followup"
    assert second_reply.plan["meta"]["debug_period_detected"] == {
        "month": 1,
        "year": 2026,
        "date_range": {"start_date": "2026-01-01", "end_date": "2026-01-31"},
    }

    assert len(router.calls) == 2
    assert router.calls[1] == (
        "finance_releves_sum",
        {
            "direction": "DEBIT_ONLY",
            "date_range": {
                "start_date": "2026-01-01",
                "end_date": "2026-01-31",
            },
            "categorie": "Loisir",
        },
    )
    assert second_reply.should_update_active_task is False
    assert second_reply.active_task is None
