"""Minimal deterministic agent loop (no LLM calls)."""

from __future__ import annotations

from dataclasses import dataclass

from agent.planner import ClarificationPlan, NoopPlan, ToolCallPlan, plan_from_message
from agent.tool_router import ToolRouter


@dataclass(slots=True)
class AgentReply:
    """Serializable chat output for API responses."""

    reply: str
    tool_result: dict[str, object] | None = None


@dataclass(slots=True)
class AgentLoop:
    tool_router: ToolRouter

    def handle_user_message(self, message: str) -> AgentReply:
        plan = plan_from_message(message)

        if isinstance(plan, ToolCallPlan):
            result = self.tool_router.call(plan.tool_name, plan.payload)
            return AgentReply(
                reply=plan.user_reply,
                tool_result=result.model_dump(mode="json"),
            )

        if isinstance(plan, ClarificationPlan):
            return AgentReply(reply=plan.question)

        if isinstance(plan, NoopPlan):
            return AgentReply(reply=plan.reply)

        return AgentReply(reply="Commandes disponibles: 'ping' ou 'search: <term>'.")
