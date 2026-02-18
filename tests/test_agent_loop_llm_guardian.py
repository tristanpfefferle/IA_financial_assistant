from __future__ import annotations

from uuid import UUID

import agent.loop
from agent.loop import AgentLoop
from agent.llm_judge import LLMJudgeResult
from agent.memory import QueryMemory
from agent.planner import ToolCallPlan


class _Router:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
        self.calls.append((tool_name, payload))
        return {"ok": True}


class _JudgeStub:
    def __init__(self, result: LLMJudgeResult) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def judge(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def _followup_sum_plan(*_args, **_kwargs):
    return ToolCallPlan(
        tool_name="finance_releves_sum",
        payload={
            "direction": "DEBIT_ONLY",
            "categorie": "Loisir",
            "date_range": {"start_date": "2025-12-01", "end_date": "2025-12-31"},
        },
        user_reply="OK.",
        meta={"followup_from_memory": True},
    )


def _enable_llm(monkeypatch) -> None:
    monkeypatch.setattr(agent.loop.config, "llm_enabled", lambda: True)
    monkeypatch.setattr(
        agent.loop.config,
        "llm_allowed_tools",
        lambda: {"finance_releves_sum", "finance_releves_search", "finance_releves_aggregate"},
    )


def test_low_confidence_plan_calls_llm_judge(monkeypatch) -> None:
    _enable_llm(monkeypatch)
    router = _Router()
    judge = _JudgeStub(LLMJudgeResult(verdict="approve"))
    loop = AgentLoop(tool_router=router, llm_judge=judge)

    memory = {
        "last_query": QueryMemory(
            date_range={"start_date": "2025-12-01", "end_date": "2025-12-31"},
            last_tool_name="finance_releves_sum",
            filters={"categorie": "Loisir"},
        ).to_dict()
    }
    monkeypatch.setattr(agent.loop, "followup_plan_from_message", _followup_sum_plan)
    reply = loop.handle_user_message("et en janvier 2026", memory=memory)

    assert judge.calls, "LLM guardian should be called for low-confidence follow-up"
    assert router.calls
    assert reply.plan is not None


def test_llm_guardian_repair_non_allowlist_returns_clarification(monkeypatch) -> None:
    _enable_llm(monkeypatch)
    monkeypatch.setattr(agent.loop.config, "llm_allowed_tools", lambda: {"finance_releves_sum"})
    router = _Router()
    judge = _JudgeStub(
        LLMJudgeResult(
            verdict="repair",
            tool_name="finance_categories_delete",
            payload={"category_name": "Loisir"},
            user_reply="OK.",
        )
    )
    loop = AgentLoop(tool_router=router, llm_judge=judge)

    memory = {
        "last_query": QueryMemory(
            date_range={"start_date": "2025-12-01", "end_date": "2025-12-31"},
            last_tool_name="finance_releves_sum",
            filters={"categorie": "Loisir"},
        ).to_dict()
    }
    monkeypatch.setattr(agent.loop, "followup_plan_from_message", _followup_sum_plan)
    reply = loop.handle_user_message("et en janvier 2026", memory=memory)

    assert "préciser" in reply.reply.lower()
    assert not router.calls


def test_followup_january_2026_keeps_category_and_updates_period(monkeypatch) -> None:
    _enable_llm(monkeypatch)
    router = _Router()
    judge = _JudgeStub(
        LLMJudgeResult(
            verdict="repair",
            tool_name="finance_releves_sum",
            payload={
                "direction": "DEBIT_ONLY",
                "categorie": "Loisir",
                "date_range": {"start_date": "2026-01-01", "end_date": "2026-01-31"},
            },
            user_reply="OK.",
        )
    )
    loop = AgentLoop(tool_router=router, llm_judge=judge)

    def _wrong_followup(_message, _memory, *, known_categories=None):
        del known_categories
        return ToolCallPlan(
            tool_name="finance_releves_sum",
            payload={
                "direction": "DEBIT_ONLY",
                "categorie": "Loisir",
                "date_range": {"start_date": "2025-12-01", "end_date": "2025-12-31"},
            },
            user_reply="OK.",
            meta={"followup_from_memory": True},
        )

    monkeypatch.setattr(agent.loop, "followup_plan_from_message", _wrong_followup)

    memory = {
        "last_query": QueryMemory(
            date_range={"start_date": "2025-12-01", "end_date": "2025-12-31"},
            last_tool_name="finance_releves_sum",
            filters={"categorie": "Loisir"},
        ).to_dict()
    }
    loop.handle_user_message("et en janvier 2026", memory=memory)

    assert router.calls
    tool_name, payload = router.calls[0]
    assert tool_name == "finance_releves_sum"
    assert payload["categorie"] == "Loisir"
    assert payload["date_range"] == {"start_date": "2026-01-01", "end_date": "2026-01-31"}


def test_low_confidence_wrong_deterministic_plan_is_repaired(monkeypatch) -> None:
    _enable_llm(monkeypatch)
    router = _Router()
    judge = _JudgeStub(
        LLMJudgeResult(
            verdict="repair",
            tool_name="finance_releves_sum",
            payload={
                "direction": "DEBIT_ONLY",
                "date_range": {"start_date": "2026-01-01", "end_date": "2026-01-31"},
            },
        )
    )
    loop = AgentLoop(tool_router=router, llm_judge=judge)

    def _wrong_followup(_message, _memory, *, known_categories=None):
        del known_categories
        return ToolCallPlan(
            tool_name="finance_releves_sum",
            payload={
                "direction": "DEBIT_ONLY",
                "date_range": {"start_date": "2025-12-01", "end_date": "2025-12-31"},
            },
            user_reply="OK.",
            meta={"followup_from_memory": True},
        )

    monkeypatch.setattr(agent.loop, "followup_plan_from_message", _wrong_followup)

    memory = {
        "last_query": QueryMemory(
            date_range={"start_date": "2025-12-01", "end_date": "2025-12-31"},
            last_tool_name="finance_releves_sum",
            filters={},
        ).to_dict()
    }
    loop.handle_user_message("et en janvier 2026", memory=memory)

    assert router.calls[0][1]["date_range"] == {"start_date": "2026-01-01", "end_date": "2026-01-31"}
