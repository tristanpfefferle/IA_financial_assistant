"""Minimal deterministic agent loop (no LLM calls)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel

from agent.answer_builder import build_final_reply
from agent.llm_planner import LLMPlanner
from agent.planner import (
    ClarificationPlan,
    ErrorPlan,
    NoopPlan,
    SetActiveTaskPlan,
    ToolCallPlan,
    plan_from_message,
)
from agent.tool_router import ToolRouter
from shared.models import ToolError, ToolErrorCode


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AgentReply:
    """Serializable chat output for API responses."""

    reply: str
    tool_result: dict[str, object] | None = None
    plan: dict[str, object] | None = None
    active_task: dict[str, object] | None = None
    should_update_active_task: bool = False


@dataclass(slots=True)
class AgentLoop:
    tool_router: ToolRouter
    llm_planner: LLMPlanner | None = None

    @staticmethod
    def _normalize_tool_result(tool_name: str, result: object) -> object:
        if result is not None:
            return result

        if tool_name == "finance_categories_delete":
            logger.info("tool_returned_none_defaulting_ok tool_name=%s", tool_name)
            return {"ok": True}

        logger.warning("tool_returned_none_backend_error tool_name=%s", tool_name)
        return ToolError(
            code=ToolErrorCode.BACKEND_ERROR,
            message=f"Tool {tool_name} returned no result",
        )

    @staticmethod
    def _serialize_tool_result(result: object) -> dict[str, object] | None:
        if result is None:
            return None
        if isinstance(result, BaseModel):
            return result.model_dump(mode="json")
        if isinstance(result, dict):
            return result
        return {"value": str(result)}

    def handle_user_message(
        self,
        message: str,
        *,
        profile_id: UUID | None = None,
        active_task: dict[str, object] | None = None,
    ) -> AgentReply:
        plan = plan_from_message(message, llm_planner=self.llm_planner, active_task=active_task)
        has_pending_delete_confirmation = active_task is not None and active_task.get("type") == "confirm_delete_category"

        if isinstance(plan, SetActiveTaskPlan):
            return AgentReply(
                reply=plan.reply,
                active_task=plan.active_task,
                should_update_active_task=True,
            )

        if isinstance(plan, ToolCallPlan):
            logger.info("tool_execution_started tool_name=%s", plan.tool_name)
            raw_result = self.tool_router.call(plan.tool_name, plan.payload, profile_id=profile_id)
            result = self._normalize_tool_result(plan.tool_name, raw_result)
            final_reply = build_final_reply(plan=plan, tool_result=result)
            logger.info("tool_execution_completed tool_name=%s", plan.tool_name)
            return AgentReply(
                reply=final_reply,
                tool_result=self._serialize_tool_result(result),
                plan={"tool_name": plan.tool_name, "payload": plan.payload},
                active_task=None if has_pending_delete_confirmation else active_task,
                should_update_active_task=has_pending_delete_confirmation,
            )

        if isinstance(plan, ClarificationPlan):
            return AgentReply(reply=plan.question)

        if isinstance(plan, NoopPlan):
            return AgentReply(
                reply=plan.reply,
                active_task=None if has_pending_delete_confirmation else active_task,
                should_update_active_task=has_pending_delete_confirmation,
            )

        if isinstance(plan, ErrorPlan):
            return AgentReply(
                reply=plan.reply,
                tool_result=plan.tool_error.model_dump(mode="json"),
            )

        return AgentReply(reply="Commandes disponibles: 'ping' ou 'search: <term>'.")
