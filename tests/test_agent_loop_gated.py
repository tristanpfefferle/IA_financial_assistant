"""Integration-style tests for gated LLM execution in AgentLoop."""

from __future__ import annotations

from uuid import UUID

import pytest

import agent.loop
from agent.loop import AgentLoop
from agent.planner import ClarificationPlan, ErrorPlan, NoopPlan, ToolCallPlan
from shared.models import ToolError, ToolErrorCode


class _RouterSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
        self.calls.append((tool_name, payload))
        return {"ok": True}


def _configure_gated(monkeypatch, *, allowlist: set[str], deterministic_plan, llm_plan):
    monkeypatch.setattr(agent.loop, "parse_intent", lambda _message: None)
    monkeypatch.setattr(
        agent.loop,
        "deterministic_plan_from_message",
        lambda _message: deterministic_plan,
    )
    monkeypatch.setattr(agent.loop, "plan_from_message", lambda *_args, **_kwargs: llm_plan)
    monkeypatch.setattr(agent.loop.config, "llm_enabled", lambda: True)
    monkeypatch.setattr(agent.loop.config, "llm_gated", lambda: True)
    monkeypatch.setattr(agent.loop.config, "llm_shadow", lambda: False)
    monkeypatch.setattr(agent.loop.config, "llm_allowed_tools", lambda: allowlist)


def test_gated_allows_releves_search_with_normalized_payload(monkeypatch) -> None:
    router = _RouterSpy()
    _configure_gated(
        monkeypatch,
        allowlist={"finance_releves_search"},
        deterministic_plan=NoopPlan(reply="deterministic"),
        llm_plan=ToolCallPlan(
            tool_name="finance_releves_search",
            payload={"merchant": "  migros  "},
            user_reply="OK.",
        ),
    )

    reply = AgentLoop(tool_router=router, llm_planner=object()).handle_user_message(
        "montre mes transactions migros"
    )

    assert router.calls == [
        ("finance_releves_search", {"merchant": "migros", "limit": 50, "offset": 0})
    ]
    assert reply.plan == {
        "tool_name": "finance_releves_search",
        "payload": {"merchant": "migros", "limit": 50, "offset": 0},
    }


def test_gated_allows_bank_accounts_list(monkeypatch) -> None:
    router = _RouterSpy()
    _configure_gated(
        monkeypatch,
        allowlist={"finance_bank_accounts_list"},
        deterministic_plan=NoopPlan(reply="deterministic"),
        llm_plan=ToolCallPlan(
            tool_name="finance_bank_accounts_list",
            payload={},
            user_reply="OK.",
        ),
    )

    AgentLoop(tool_router=router, llm_planner=object()).handle_user_message(
        "montre moi mes comptes bancaires"
    )

    assert router.calls == [("finance_bank_accounts_list", {})]


@pytest.mark.parametrize(
    "payload",
    [{"merchant": ""}, {}, {"merchant": "   "}],
)
def test_gated_invalid_releves_search_payload_falls_back(monkeypatch, payload: dict) -> None:
    router = _RouterSpy()
    _configure_gated(
        monkeypatch,
        allowlist={"finance_releves_search"},
        deterministic_plan=NoopPlan(reply="deterministic"),
        llm_plan=ToolCallPlan(
            tool_name="finance_releves_search",
            payload=payload,
            user_reply="OK.",
        ),
    )

    reply = AgentLoop(tool_router=router, llm_planner=object()).handle_user_message("query")

    assert router.calls == []
    assert reply.reply == "deterministic"


def test_gated_invalid_releves_search_payload_type_falls_back(monkeypatch) -> None:
    router = _RouterSpy()
    _configure_gated(
        monkeypatch,
        allowlist={"finance_releves_search"},
        deterministic_plan=NoopPlan(reply="deterministic"),
        llm_plan=ToolCallPlan(
            tool_name="finance_releves_search",
            payload={"merchant": 123},
            user_reply="OK.",
        ),
    )

    reply = AgentLoop(tool_router=router, llm_planner=object()).handle_user_message("query")

    assert router.calls == []
    assert reply.reply == "deterministic"


def test_gated_bank_accounts_list_payload_is_normalized_to_empty_dict(monkeypatch) -> None:
    router = _RouterSpy()
    _configure_gated(
        monkeypatch,
        allowlist={"finance_bank_accounts_list"},
        deterministic_plan=NoopPlan(reply="deterministic"),
        llm_plan=ToolCallPlan(
            tool_name="finance_bank_accounts_list",
            payload={"x": 1},
            user_reply="OK.",
        ),
    )

    reply = AgentLoop(tool_router=router, llm_planner=object()).handle_user_message("query")

    assert router.calls == [("finance_bank_accounts_list", {})]
    assert reply.plan == {"tool_name": "finance_bank_accounts_list", "payload": {}}


def test_gated_categories_list_payload_is_normalized_to_empty_dict(monkeypatch) -> None:
    router = _RouterSpy()
    _configure_gated(
        monkeypatch,
        allowlist={"finance_categories_list"},
        deterministic_plan=NoopPlan(reply="deterministic"),
        llm_plan=ToolCallPlan(
            tool_name="finance_categories_list",
            payload={"x": 1},
            user_reply="OK.",
        ),
    )

    reply = AgentLoop(tool_router=router, llm_planner=object()).handle_user_message("query")

    assert router.calls == [("finance_categories_list", {})]
    assert reply.plan == {"tool_name": "finance_categories_list", "payload": {}}


def test_gated_invalid_profile_get_payload_falls_back(monkeypatch) -> None:
    router = _RouterSpy()
    _configure_gated(
        monkeypatch,
        allowlist={"finance_profile_get"},
        deterministic_plan=NoopPlan(reply="deterministic"),
        llm_plan=ToolCallPlan(
            tool_name="finance_profile_get",
            payload={},
            user_reply="OK.",
        ),
    )

    reply = AgentLoop(tool_router=router, llm_planner=object()).handle_user_message("query")

    assert router.calls == []
    assert reply.reply == "deterministic"


@pytest.mark.parametrize("tool_name", ["finance_bank_accounts_delete", "finance_categories_delete"])
def test_gated_blocks_tool_outside_allowlist(monkeypatch, tool_name: str) -> None:
    router = _RouterSpy()
    _configure_gated(
        monkeypatch,
        allowlist={"finance_releves_search"},
        deterministic_plan=NoopPlan(reply="deterministic"),
        llm_plan=ToolCallPlan(tool_name=tool_name, payload={"name": "test"}, user_reply="OK."),
    )

    reply = AgentLoop(tool_router=router, llm_planner=object()).handle_user_message("query")

    assert router.calls == []
    assert reply.reply == "deterministic"


def test_gated_llm_write_requires_confirmation_does_not_execute_immediately(
    monkeypatch, caplog
) -> None:
    router = _RouterSpy()
    _configure_gated(
        monkeypatch,
        allowlist={"finance_categories_delete"},
        deterministic_plan=NoopPlan(reply="deterministic"),
        llm_plan=ToolCallPlan(
            tool_name="finance_categories_delete",
            payload={"category_name": "  restaurants  "},
            user_reply="OK.",
        ),
    )

    with caplog.at_level("INFO"):
        reply = AgentLoop(tool_router=router, llm_planner=object()).handle_user_message(
            "supprime la catégorie restaurants"
        )

    assert router.calls == []
    assert reply.active_task is not None
    assert reply.active_task["type"] == "needs_confirmation"
    assert reply.active_task["confirmation_type"] == "confirm_llm_write"
    assert reply.active_task["context"]["tool_name"] == "finance_categories_delete"
    assert "category_name" in reply.active_task["context"]["payload"]
    assert "Confirmez-vous ? (oui/non)" in reply.reply
    assert any(record.msg == "llm_tool_requires_confirmation" for record in caplog.records)


def test_gated_llm_write_invalid_categories_delete_payload_falls_back(monkeypatch) -> None:
    router = _RouterSpy()
    _configure_gated(
        monkeypatch,
        allowlist={"finance_categories_delete"},
        deterministic_plan=NoopPlan(reply="deterministic"),
        llm_plan=ToolCallPlan(
            tool_name="finance_categories_delete",
            payload={"category_name": "   "},
            user_reply="OK.",
        ),
    )

    reply = AgentLoop(tool_router=router, llm_planner=object()).handle_user_message("query")

    assert reply.reply == "deterministic"
    assert router.calls == []
    assert reply.active_task is None


@pytest.mark.parametrize("payload", [{}, {"set": {}}])
def test_gated_llm_write_invalid_profile_update_payload_falls_back(
    monkeypatch, payload: dict
) -> None:
    router = _RouterSpy()
    _configure_gated(
        monkeypatch,
        allowlist={"finance_profile_update"},
        deterministic_plan=NoopPlan(reply="deterministic"),
        llm_plan=ToolCallPlan(
            tool_name="finance_profile_update",
            payload=payload,
            user_reply="OK.",
        ),
    )

    reply = AgentLoop(tool_router=router, llm_planner=object()).handle_user_message("query")

    assert reply.reply == "deterministic"
    assert router.calls == []
    assert reply.active_task is None


def test_gated_llm_write_profile_update_disallows_unknown_fields_falls_back(
    monkeypatch,
) -> None:
    router = _RouterSpy()
    _configure_gated(
        monkeypatch,
        allowlist={"finance_profile_update"},
        deterministic_plan=NoopPlan(reply="deterministic"),
        llm_plan=ToolCallPlan(
            tool_name="finance_profile_update",
            payload={"set": {"ville_de_residence": "x"}},
            user_reply="OK.",
        ),
    )

    reply = AgentLoop(tool_router=router, llm_planner=object()).handle_user_message("query")

    assert reply.reply == "deterministic"
    assert router.calls == []
    assert reply.active_task is None


def test_confirm_llm_write_yes_executes_tool(monkeypatch) -> None:
    router = _RouterSpy()
    _configure_gated(
        monkeypatch,
        allowlist={"finance_categories_delete"},
        deterministic_plan=NoopPlan(reply="deterministic"),
        llm_plan=ToolCallPlan(
            tool_name="finance_categories_delete",
            payload={"category_name": "autres"},
            user_reply="OK.",
        ),
    )

    loop = AgentLoop(tool_router=router, llm_planner=object())
    first_reply = loop.handle_user_message("supprime la catégorie autres")
    second_reply = loop.handle_user_message("oui", active_task=first_reply.active_task)

    assert router.calls == [("finance_categories_delete", {"category_name": "autres"})]
    assert second_reply.plan == {
        "tool_name": "finance_categories_delete",
        "payload": {"category_name": "autres"},
    }
    assert second_reply.should_update_active_task is True
    assert second_reply.active_task is None


def test_confirm_llm_write_yes_executes_profile_update(monkeypatch) -> None:
    router = _RouterSpy()
    _configure_gated(
        monkeypatch,
        allowlist={"finance_profile_update"},
        deterministic_plan=NoopPlan(reply="deterministic"),
        llm_plan=ToolCallPlan(
            tool_name="finance_profile_update",
            payload={"set": {"city": "  Lausanne  ", "country": " CH "}},
            user_reply="OK.",
        ),
    )

    loop = AgentLoop(tool_router=router, llm_planner=object())
    first_reply = loop.handle_user_message("mets à jour mon profil")
    second_reply = loop.handle_user_message("oui", active_task=first_reply.active_task)

    assert first_reply.active_task is not None
    assert first_reply.active_task["type"] == "needs_confirmation"
    assert router.calls == [
        (
            "finance_profile_update",
            {"set": {"city": "Lausanne", "country": "CH"}},
        )
    ]
    assert second_reply.plan == {
        "tool_name": "finance_profile_update",
        "payload": {"set": {"city": "Lausanne", "country": "CH"}},
    }


def test_gated_llm_write_profile_update_normalizes_french_alias_before_confirmation(
    monkeypatch,
) -> None:
    router = _RouterSpy()
    _configure_gated(
        monkeypatch,
        allowlist={"finance_profile_update"},
        deterministic_plan=NoopPlan(reply="deterministic"),
        llm_plan=ToolCallPlan(
            tool_name="finance_profile_update",
            payload={"set": {"ville": "  Choëx "}},
            user_reply="OK.",
        ),
    )

    loop = AgentLoop(tool_router=router, llm_planner=object())
    first_reply = loop.handle_user_message("Mets à jour mon profil : ville Choëx")

    assert first_reply.active_task is not None
    assert first_reply.active_task["type"] == "needs_confirmation"
    assert first_reply.active_task["confirmation_type"] == "confirm_llm_write"
    assert first_reply.active_task["context"]["payload"] == {"set": {"city": "Choëx"}}

    second_reply = loop.handle_user_message("oui", active_task=first_reply.active_task)

    assert router.calls == [("finance_profile_update", {"set": {"city": "Choëx"}})]
    assert second_reply.plan == {
        "tool_name": "finance_profile_update",
        "payload": {"set": {"city": "Choëx"}},
    }



def test_gated_llm_write_profile_update_normalizes_english_aliases(monkeypatch) -> None:
    router = _RouterSpy()
    _configure_gated(
        monkeypatch,
        allowlist={"finance_profile_update"},
        deterministic_plan=NoopPlan(reply="deterministic"),
        llm_plan=ToolCallPlan(
            tool_name="finance_profile_update",
            payload={"set": {"zip": " 1000 ", "country": " ch "}},
            user_reply="OK.",
        ),
    )

    loop = AgentLoop(tool_router=router, llm_planner=object())
    first_reply = loop.handle_user_message("mets à jour mon profil")
    second_reply = loop.handle_user_message("oui", active_task=first_reply.active_task)

    assert first_reply.active_task is not None
    assert first_reply.active_task["context"]["payload"] == {
        "set": {"postal_code": "1000", "country": "ch"}
    }
    assert router.calls == [
        ("finance_profile_update", {"set": {"postal_code": "1000", "country": "ch"}})
    ]
    assert second_reply.plan == {
        "tool_name": "finance_profile_update",
        "payload": {"set": {"postal_code": "1000", "country": "ch"}},
    }


def test_confirm_llm_write_yes_executes_profile_update_with_canonical_postal_code(
    monkeypatch,
) -> None:
    router = _RouterSpy()
    _configure_gated(
        monkeypatch,
        allowlist={"finance_profile_update"},
        deterministic_plan=NoopPlan(reply="deterministic"),
        llm_plan=ToolCallPlan(
            tool_name="finance_profile_update",
            payload={"set": {"postal_code": " 1000 "}},
            user_reply="OK.",
        ),
    )

    loop = AgentLoop(tool_router=router, llm_planner=object())
    first_reply = loop.handle_user_message("mets à jour mon code postal")

    assert first_reply.active_task is not None
    assert first_reply.active_task["type"] == "needs_confirmation"
    assert first_reply.active_task["confirmation_type"] == "confirm_llm_write"
    assert first_reply.active_task["context"]["payload"] == {"set": {"postal_code": "1000"}}

    second_reply = loop.handle_user_message("oui", active_task=first_reply.active_task)

    assert router.calls == [("finance_profile_update", {"set": {"postal_code": "1000"}})]
    assert second_reply.plan == {
        "tool_name": "finance_profile_update",
        "payload": {"set": {"postal_code": "1000"}},
    }


def test_confirm_llm_write_no_cancels(monkeypatch) -> None:
    router = _RouterSpy()
    _configure_gated(
        monkeypatch,
        allowlist={"finance_categories_delete"},
        deterministic_plan=NoopPlan(reply="deterministic"),
        llm_plan=ToolCallPlan(
            tool_name="finance_categories_delete",
            payload={"category_name": "autres"},
            user_reply="OK.",
        ),
    )

    loop = AgentLoop(tool_router=router, llm_planner=object())
    first_reply = loop.handle_user_message("supprime la catégorie autres")
    second_reply = loop.handle_user_message("non", active_task=first_reply.active_task)

    assert router.calls == []
    assert second_reply.reply == "Action annulée."
    assert second_reply.should_update_active_task is True
    assert second_reply.active_task is None


def test_deterministic_tool_call_wins_and_llm_is_not_called(monkeypatch) -> None:
    router = _RouterSpy()
    llm_calls = {"count": 0}

    monkeypatch.setattr(agent.loop, "parse_intent", lambda _message: None)
    monkeypatch.setattr(
        agent.loop,
        "deterministic_plan_from_message",
        lambda _message: ToolCallPlan(
            tool_name="finance_releves_search",
            payload={"merchant": "deterministic", "limit": 50, "offset": 0},
            user_reply="OK.",
        ),
    )

    def _llm(*_args, **_kwargs):
        llm_calls["count"] += 1
        return ToolCallPlan(
            tool_name="finance_releves_search",
            payload={"merchant": "llm"},
            user_reply="OK.",
        )

    monkeypatch.setattr(agent.loop, "plan_from_message", _llm)
    monkeypatch.setattr(agent.loop.config, "llm_enabled", lambda: True)
    monkeypatch.setattr(agent.loop.config, "llm_gated", lambda: True)
    monkeypatch.setattr(agent.loop.config, "llm_shadow", lambda: False)
    monkeypatch.setattr(agent.loop.config, "llm_allowed_tools", lambda: {"finance_releves_search"})

    AgentLoop(tool_router=router, llm_planner=object()).handle_user_message("query")

    assert llm_calls["count"] == 0
    assert router.calls == [
        ("finance_releves_search", {"merchant": "deterministic", "limit": 50, "offset": 0})
    ]


def test_deterministic_clarification_wins_and_llm_is_not_called(monkeypatch) -> None:
    llm_calls = {"count": 0}
    monkeypatch.setattr(agent.loop, "parse_intent", lambda _message: None)
    monkeypatch.setattr(
        agent.loop,
        "deterministic_plan_from_message",
        lambda _message: ClarificationPlan(question="clarify"),
    )

    def _llm(*_args, **_kwargs):
        llm_calls["count"] += 1
        return NoopPlan(reply="noop")

    monkeypatch.setattr(agent.loop, "plan_from_message", _llm)
    monkeypatch.setattr(agent.loop.config, "llm_enabled", lambda: True)
    monkeypatch.setattr(agent.loop.config, "llm_gated", lambda: True)

    reply = AgentLoop(tool_router=_RouterSpy(), llm_planner=object()).handle_user_message("query")

    assert llm_calls["count"] == 0
    assert reply.reply == "clarify"


def test_gated_allows_releves_sum_with_direction(monkeypatch) -> None:
    router = _RouterSpy()
    _configure_gated(
        monkeypatch,
        allowlist={"finance_releves_sum"},
        deterministic_plan=NoopPlan(reply="deterministic"),
        llm_plan=ToolCallPlan(
            tool_name="finance_releves_sum",
            payload={"direction": "DEBIT_ONLY", "merchant": "migros"},
            user_reply="OK.",
        ),
    )

    AgentLoop(tool_router=router, llm_planner=object()).handle_user_message(
        "combien dépensé migros mois dernier"
    )

    assert router.calls == [
        ("finance_releves_sum", {"direction": "DEBIT_ONLY", "merchant": "migros"})
    ]


def test_gated_blocks_releves_sum_outside_allowlist(monkeypatch) -> None:
    router = _RouterSpy()
    _configure_gated(
        monkeypatch,
        allowlist={"finance_releves_search"},
        deterministic_plan=NoopPlan(reply="deterministic"),
        llm_plan=ToolCallPlan(
            tool_name="finance_releves_sum",
            payload={"direction": "DEBIT_ONLY"},
            user_reply="OK.",
        ),
    )

    reply = AgentLoop(tool_router=router, llm_planner=object()).handle_user_message("query")

    assert router.calls == []
    assert reply.reply == "deterministic"


def test_gated_invalid_releves_sum_payload_falls_back(monkeypatch) -> None:
    router = _RouterSpy()
    _configure_gated(
        monkeypatch,
        allowlist={"finance_releves_sum"},
        deterministic_plan=NoopPlan(reply="deterministic"),
        llm_plan=ToolCallPlan(
            tool_name="finance_releves_sum",
            payload={},
            user_reply="OK.",
        ),
    )

    reply = AgentLoop(tool_router=router, llm_planner=object()).handle_user_message("query")

    assert router.calls == []
    assert reply.reply == "deterministic"


def test_gated_allows_releves_aggregate_with_valid_payload(monkeypatch) -> None:
    router = _RouterSpy()
    _configure_gated(
        monkeypatch,
        allowlist={"finance_releves_aggregate"},
        deterministic_plan=NoopPlan(reply="deterministic"),
        llm_plan=ToolCallPlan(
            tool_name="finance_releves_aggregate",
            payload={"group_by": "categorie", "direction": "DEBIT_ONLY"},
            user_reply="OK.",
        ),
    )

    AgentLoop(tool_router=router, llm_planner=object()).handle_user_message("query")

    assert router.calls == [
        (
            "finance_releves_aggregate",
            {"group_by": "categorie", "direction": "DEBIT_ONLY"},
        )
    ]


def test_gated_invalid_releves_aggregate_payload_falls_back(monkeypatch) -> None:
    router = _RouterSpy()
    _configure_gated(
        monkeypatch,
        allowlist={"finance_releves_aggregate"},
        deterministic_plan=NoopPlan(reply="deterministic"),
        llm_plan=ToolCallPlan(
            tool_name="finance_releves_aggregate",
            payload={"group_by": ""},
            user_reply="OK.",
        ),
    )

    reply = AgentLoop(tool_router=router, llm_planner=object()).handle_user_message("query")

    assert router.calls == []
    assert reply.reply == "deterministic"


def test_gated_logs_tool_blocked_event(monkeypatch, caplog) -> None:
    router = _RouterSpy()
    _configure_gated(
        monkeypatch,
        allowlist={"finance_releves_search"},
        deterministic_plan=NoopPlan(reply="deterministic"),
        llm_plan=ToolCallPlan(
            tool_name="finance_categories_delete",
            payload={"category_name": "test"},
            user_reply="OK.",
        ),
    )

    with caplog.at_level("INFO"):
        AgentLoop(tool_router=router, llm_planner=object()).handle_user_message("query")

    assert any(record.msg == "llm_gated_used" for record in caplog.records)
    assert any(record.msg == "llm_tool_blocked" for record in caplog.records)


def test_gated_logs_payload_invalid_event(monkeypatch, caplog) -> None:
    router = _RouterSpy()
    _configure_gated(
        monkeypatch,
        allowlist={"finance_releves_search"},
        deterministic_plan=NoopPlan(reply="deterministic"),
        llm_plan=ToolCallPlan(
            tool_name="finance_releves_search",
            payload={"merchant": ""},
            user_reply="OK.",
        ),
    )

    with caplog.at_level("INFO"):
        AgentLoop(tool_router=router, llm_planner=object()).handle_user_message("query")

    assert any(record.msg == "llm_payload_invalid" for record in caplog.records)


def test_gated_logs_tool_allowed_event(monkeypatch, caplog) -> None:
    router = _RouterSpy()
    _configure_gated(
        monkeypatch,
        allowlist={"finance_bank_accounts_list"},
        deterministic_plan=NoopPlan(reply="deterministic"),
        llm_plan=ToolCallPlan(
            tool_name="finance_bank_accounts_list",
            payload={},
            user_reply="OK.",
        ),
    )

    with caplog.at_level("INFO"):
        AgentLoop(tool_router=router, llm_planner=object()).handle_user_message("query")

    assert any(record.msg == "llm_tool_allowed" for record in caplog.records)


def test_gated_llm_exception_falls_back_and_logs_error(monkeypatch, caplog) -> None:
    router = _RouterSpy()
    monkeypatch.setattr(agent.loop, "parse_intent", lambda _message: None)
    monkeypatch.setattr(
        agent.loop,
        "deterministic_plan_from_message",
        lambda _message: NoopPlan(reply="deterministic"),
    )

    def _raise_plan_from_message(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(agent.loop, "plan_from_message", _raise_plan_from_message)
    monkeypatch.setattr(agent.loop.config, "llm_enabled", lambda: True)
    monkeypatch.setattr(agent.loop.config, "llm_gated", lambda: True)
    monkeypatch.setattr(agent.loop.config, "llm_shadow", lambda: False)

    with caplog.at_level("ERROR"):
        reply = AgentLoop(tool_router=router, llm_planner=object()).handle_user_message(
            "query"
        )

    assert reply.reply == "deterministic"
    assert any(
        record.msg == "llm_gated_error"
        or getattr(record, "event", None) == "llm_gated_error"
        for record in caplog.records
    )


def test_deterministic_error_plan_wins_over_llm(monkeypatch) -> None:
    llm_calls = {"count": 0}
    monkeypatch.setattr(agent.loop, "parse_intent", lambda _message: None)
    monkeypatch.setattr(
        agent.loop,
        "deterministic_plan_from_message",
        lambda _message: ErrorPlan(
            reply="deterministic-error",
            tool_error=ToolError(code=ToolErrorCode.BACKEND_ERROR, message="backend"),
        ),
    )

    def _llm(*_args, **_kwargs):
        llm_calls["count"] += 1
        return NoopPlan(reply="noop")

    monkeypatch.setattr(agent.loop, "plan_from_message", _llm)
    monkeypatch.setattr(agent.loop.config, "llm_enabled", lambda: True)
    monkeypatch.setattr(agent.loop.config, "llm_gated", lambda: True)

    reply = AgentLoop(tool_router=_RouterSpy(), llm_planner=object()).handle_user_message("query")

    assert llm_calls["count"] == 0
    assert reply.reply == "deterministic-error"
