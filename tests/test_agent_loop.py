"""Tests for AgentLoop active_task prioritization."""

from __future__ import annotations

from uuid import UUID

from agent.loop import AgentLoop


class _FailIfCalledRouter:
    def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
        raise AssertionError(f"Unexpected tool call: {tool_name} {payload}")


class _DeleteRouter:
    def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
        assert tool_name == "finance_categories_delete"
        assert payload == {"category_name": "autres"}
        return {"ok": True}


def test_confirm_delete_category_yes_executes_delete() -> None:
    loop = AgentLoop(tool_router=_DeleteRouter())

    reply = loop.handle_user_message(
        "Oui",
        active_task={"type": "confirm_delete_category", "category_name": "autres"},
    )

    assert reply.plan == {"tool_name": "finance_categories_delete", "payload": {"category_name": "autres"}}
    assert reply.should_update_active_task is True
    assert reply.active_task is None


def test_confirm_delete_category_no_cancels() -> None:
    loop = AgentLoop(tool_router=_FailIfCalledRouter())

    reply = loop.handle_user_message(
        "non",
        active_task={"type": "confirm_delete_category", "category_name": "autres"},
    )

    assert reply.reply == "Suppression annulée."
    assert reply.should_update_active_task is True
    assert reply.active_task is None


def test_confirm_delete_category_invalid_prompts_again() -> None:
    active_task = {"type": "confirm_delete_category", "category_name": "autres"}
    loop = AgentLoop(tool_router=_FailIfCalledRouter())

    reply = loop.handle_user_message("peut-être", active_task=active_task)

    assert reply.reply == "Répondez OUI ou NON."
    assert reply.should_update_active_task is True
    assert reply.active_task == active_task
