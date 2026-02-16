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
    deterministic_plan_from_message,
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
    def plan_from_active_task(message: str, active_task: dict[str, object]):
        active_task_type = active_task.get("type")
        if active_task_type != "confirm_delete_category":
            return ClarificationPlan(question="Je n'ai pas compris la tâche en attente.", meta={"keep_active_task": True})

        category_name = str(active_task.get("category_name", "")).strip()
        if not category_name:
            return NoopPlan(reply="Suppression annulée.", meta={"clear_active_task": True})

        normalized = message.strip().lower()
        if normalized in {"oui", "o", "ok", "confirme", "confirmé", "confirmée"}:
            return ToolCallPlan(
                tool_name="finance_categories_delete",
                payload={"category_name": category_name},
                user_reply="Catégorie supprimée.",
                meta={"clear_active_task": True},
            )

        if normalized in {"non", "n", "annule", "annuler"}:
            return NoopPlan(reply="Suppression annulée.", meta={"clear_active_task": True})

        return ClarificationPlan(question="Répondez OUI ou NON.", meta={"keep_active_task": True})

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
        if active_task is not None:
            plan = self.plan_from_active_task(message, active_task)
        else:
            deterministic_plan = deterministic_plan_from_message(message)
            if isinstance(deterministic_plan, (ToolCallPlan, ErrorPlan, ClarificationPlan, SetActiveTaskPlan)):
                plan = deterministic_plan
            elif isinstance(deterministic_plan, NoopPlan) and deterministic_plan.reply == "pong":
                plan = deterministic_plan
            elif self.llm_planner is not None:
                plan = plan_from_message(message, llm_planner=self.llm_planner)
            else:
                plan = deterministic_plan

        plan_meta = getattr(plan, "meta", {}) if isinstance(getattr(plan, "meta", {}), dict) else {}
        should_update_active_task = False
        updated_active_task = active_task
        if plan_meta.get("clear_active_task"):
            should_update_active_task = True
            updated_active_task = None
        elif plan_meta.get("keep_active_task"):
            should_update_active_task = True
            updated_active_task = active_task

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
                active_task=updated_active_task,
                should_update_active_task=should_update_active_task,
            )

        if isinstance(plan, ClarificationPlan):
            return AgentReply(
                reply=plan.question,
                active_task=updated_active_task,
                should_update_active_task=should_update_active_task,
            )

        if isinstance(plan, NoopPlan):
            return AgentReply(
                reply=plan.reply,
                active_task=updated_active_task,
                should_update_active_task=should_update_active_task,
            )

        if isinstance(plan, ErrorPlan):
            return AgentReply(
                reply=plan.reply,
                tool_result=plan.tool_error.model_dump(mode="json"),
            )

        return AgentReply(reply="Commandes disponibles: 'ping' ou 'search: <term>'.")
