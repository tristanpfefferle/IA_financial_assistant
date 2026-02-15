"""Minimal deterministic agent loop (no LLM calls)."""

from __future__ import annotations

from dataclasses import dataclass

from agent.llm_planner import LLMPlanner
from agent.planner import ErrorPlan, ClarificationPlan, NoopPlan, ToolCallPlan, plan_from_message
from agent.tool_router import ToolRouter


@dataclass(slots=True)
class AgentReply:
    """Serializable chat output for API responses."""

    reply: str
    tool_result: dict[str, object] | None = None


@dataclass(slots=True)
class AgentLoop:
    tool_router: ToolRouter
    llm_planner: LLMPlanner | None = None

    def handle_user_message(self, message: str) -> AgentReply:
        plan = plan_from_message(message, llm_planner=self.llm_planner)

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

        if isinstance(plan, ErrorPlan):
            return AgentReply(
                reply=plan.reply,
                tool_result=plan.tool_error.model_dump(mode="json"),
            )

        return AgentReply(reply="Commandes disponibles: 'ping' ou 'search: <term>'.")
