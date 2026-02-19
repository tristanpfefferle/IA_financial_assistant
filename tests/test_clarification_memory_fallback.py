from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

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
        if tool_name == "finance_releves_search":
            return {"items": [], "total": 0, "limit": 50, "offset": 0}
        raise AssertionError(f"unexpected tool call: {tool_name}")


def test_memory_pending_clarification_fallback_resolves_prevent_write_choice() -> None:
    router = _Router()
    loop = AgentLoop(tool_router=router)

    memory = {
        "pending_clarification": {
            "type": "clarification_pending",
            "context": {
                "clarification_type": "prevent_write_on_followup",
                "focus": "pizza",
                "period_payload": {
                    "date_range": {
                        "start_date": "2026-01-01",
                        "end_date": "2026-01-31",
                    }
                },
                "base_last_query": {
                    "filters": {"direction": "DEBIT_ONLY"},
                },
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        "last_query": {
            "last_tool_name": "finance_releves_sum",
            "date_range": {
                "start_date": "2026-01-01",
                "end_date": "2026-01-31",
            },
            "filters": {"direction": "DEBIT_ONLY"},
        },
    }

    reply = loop.handle_user_message("Marchand", profile_id=PROFILE_ID, memory=memory)

    assert reply.plan == {
        "tool_name": "finance_releves_search",
        "payload": {
            "merchant": "pizza",
            "limit": 50,
            "offset": 0,
            "direction": "DEBIT_ONLY",
            "date_range": {
                "start_date": "2026-01-01",
                "end_date": "2026-01-31",
            },
        },
    }
    assert reply.should_update_active_task is True
    assert reply.active_task is None
    assert isinstance(reply.memory_update, dict)
    assert reply.memory_update.get("pending_clarification") is None


def test_memory_pending_clarification_fallback_resolves_merchant_vs_keyword_choice() -> None:
    router = _Router()
    loop = AgentLoop(tool_router=router)

    memory = {
        "pending_clarification": {
            "type": "clarification_pending",
            "context": {
                "clarification_type": "merchant_vs_keyword",
                "merchant": "Migros",
                "keyword": "pizza",
                "period_payload": {
                    "date_range": {
                        "start_date": "2026-02-01",
                        "end_date": "2026-02-28",
                    }
                },
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        "last_query": {
            "last_tool_name": "finance_releves_search",
            "date_range": {
                "start_date": "2026-02-01",
                "end_date": "2026-02-28",
            },
            "filters": {"direction": "DEBIT_ONLY"},
        },
    }

    reply = loop.handle_user_message(
        "Marchand Migros",
        profile_id=PROFILE_ID,
        memory=memory,
    )

    assert isinstance(reply.plan, dict)
    assert reply.plan["tool_name"] == "finance_releves_search"
    assert reply.plan["payload"]["merchant"] == "migros"
    assert reply.plan["payload"]["date_range"] == {
        "start_date": "2026-02-01",
        "end_date": "2026-02-28",
    }
    assert isinstance(reply.memory_update, dict)
    assert reply.memory_update.get("pending_clarification") is None


def test_drop_stale_pending_clarification_from_memory_replaces_with_new_pending() -> None:
    router = _Router()
    loop = AgentLoop(tool_router=router)

    memory = {
        "pending_clarification": {
            "type": "clarification_pending",
            "context": {
                "clarification_type": "prevent_write_on_followup",
                "focus": "pizza",
                "period_payload": {
                    "date_range": {
                        "start_date": "2026-02-01",
                        "end_date": "2026-02-28",
                    }
                },
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        "last_query": {
            "last_tool_name": "finance_releves_search",
            "date_range": {
                "start_date": "2026-02-01",
                "end_date": "2026-02-28",
            },
            "filters": {"direction": "DEBIT_ONLY", "merchant": "pizza"},
        },
    }

    reply = loop.handle_user_message(
        "Et la pizza chez Migros ?",
        profile_id=PROFILE_ID,
        memory=memory,
    )

    assert "Tu veux chercher le marchand" in reply.reply
    assert reply.should_update_active_task is True
    assert isinstance(reply.memory_update, dict)
    pending = reply.memory_update.get("pending_clarification")
    assert isinstance(pending, dict)
    assert pending.get("type") == "clarification_pending"
    context = pending.get("context")
    assert isinstance(context, dict)
    assert context.get("clarification_type") == "merchant_vs_keyword"
    assert context.get("merchant") == "Migros"
    assert context.get("keyword") == "pizza"


def test_sanitize_sum_payload_drops_limit_and_offset() -> None:
    payload = {
        "direction": "DEBIT_ONLY",
        "date_range": {
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
        },
        "limit": 50,
        "offset": 0,
    }

    sanitized = AgentLoop._sanitize_payload_for_tool("finance_releves_sum", payload)

    assert "limit" not in sanitized
    assert "offset" not in sanitized
    assert sanitized["direction"] == "DEBIT_ONLY"
