"""Minimal deterministic agent loop (no LLM calls)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
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


_BANK_ACCOUNT_DISAMBIGUATION_TOOLS = {
    "finance_bank_accounts_delete",
    "finance_bank_accounts_set_default",
    "finance_bank_accounts_update",
}
_CONFIRM_WORDS = {"oui", "o", "ok", "confirme", "confirmé", "confirmée"}


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
        if active_task_type == "select_bank_account":
            return AgentLoop._plan_from_select_bank_account(message=message, active_task=active_task)

        if active_task_type not in {"confirm_delete_category", "confirm_delete_bank_account"}:
            return ClarificationPlan(question="Je n'ai pas compris la tâche en attente.", meta={"keep_active_task": True})

        target_name = (
            str(active_task.get("category_name", "")).strip()
            if active_task_type == "confirm_delete_category"
            else str(active_task.get("name", "")).strip()
        )
        if not target_name:
            return NoopPlan(reply="Suppression annulée.", meta={"clear_active_task": True})

        normalized = message.strip().lower()
        if normalized in _CONFIRM_WORDS:
            tool_name = "finance_categories_delete"
            payload = {"category_name": target_name}
            user_reply = "Catégorie supprimée."
            if active_task_type == "confirm_delete_bank_account":
                tool_name = "finance_bank_accounts_delete"
                payload = {"name": target_name}
                user_reply = "Compte supprimé."
            return ToolCallPlan(
                tool_name=tool_name,
                payload=payload,
                user_reply=user_reply,
                meta={"clear_active_task": True},
            )

        if normalized in {"non", "n", "annule", "annuler"}:
            return NoopPlan(reply="Suppression annulée.", meta={"clear_active_task": True})

        return ClarificationPlan(question="Répondez OUI ou NON.", meta={"keep_active_task": True})

    @staticmethod
    def _plan_from_select_bank_account(message: str, active_task: dict[str, object]):
        original_tool_name = active_task.get("original_tool_name")
        if not isinstance(original_tool_name, str) or original_tool_name not in _BANK_ACCOUNT_DISAMBIGUATION_TOOLS:
            return ClarificationPlan(
                question="Je n'ai pas compris la tâche en attente.",
                meta={"keep_active_task": True},
            )

        original_payload = active_task.get("original_payload")
        if not isinstance(original_payload, dict):
            return ClarificationPlan(
                question="Je n'ai pas compris la tâche en attente.",
                meta={"keep_active_task": True},
            )

        normalized = message.strip().lower()
        candidates_raw = active_task.get("candidates")
        suggestions_raw = active_task.get("suggestions")

        candidates: list[dict[str, str]] = []
        if isinstance(candidates_raw, list):
            for candidate in candidates_raw:
                if not isinstance(candidate, dict):
                    continue
                raw_id = candidate.get("id")
                raw_name = candidate.get("name")
                if isinstance(raw_id, str) and isinstance(raw_name, str) and raw_name.strip():
                    candidates.append({"id": raw_id, "name": raw_name})

        suggestions: list[str] = []
        if isinstance(suggestions_raw, list):
            suggestions = [name.strip() for name in suggestions_raw if isinstance(name, str) and name.strip()]

        selected_id: str | None = None
        selected_name: str | None = None

        if normalized in _CONFIRM_WORDS and suggestions:
            selected_name = suggestions[0]
        elif normalized.isdigit() and candidates:
            index = int(normalized) - 1
            if 0 <= index < len(candidates):
                selected_id = candidates[index]["id"]
        elif candidates:
            for candidate in candidates:
                if candidate["name"].strip().lower() == normalized:
                    selected_id = candidate["id"]
                    break
        if selected_name is None and selected_id is None and suggestions:
            for suggestion in suggestions:
                if suggestion.lower() == normalized:
                    selected_name = suggestion
                    break

        if selected_id is None and selected_name is None:
            return ClarificationPlan(
                question="Répondez avec un des noms proposés ou un numéro.",
                meta={"keep_active_task": True},
            )

        replay_payload = AgentLoop._build_bank_account_retry_payload(
            tool_name=original_tool_name,
            original_payload=original_payload,
            bank_account_id=selected_id,
            account_name=selected_name,
        )
        return ToolCallPlan(
            tool_name=original_tool_name,
            payload=replay_payload,
            user_reply="",
            meta={"clear_active_task": True},
        )

    @staticmethod
    def _build_bank_account_retry_payload(
        *,
        tool_name: str,
        original_payload: dict[str, object],
        bank_account_id: str | None,
        account_name: str | None,
    ) -> dict[str, object]:
        if tool_name == "finance_bank_accounts_update":
            set_payload = original_payload.get("set")
            payload: dict[str, object] = {"set": set_payload} if isinstance(set_payload, dict) else {}
            if bank_account_id is not None:
                payload["bank_account_id"] = bank_account_id
            elif account_name is not None:
                payload["name"] = account_name
            return payload

        if bank_account_id is not None:
            return {"bank_account_id": bank_account_id}
        if account_name is not None:
            return {"name": account_name}
        return dict(original_payload)

    @staticmethod
    def _build_bank_account_selection_active_task(plan: ToolCallPlan, error: ToolError) -> SetActiveTaskPlan | None:
        if plan.tool_name not in _BANK_ACCOUNT_DISAMBIGUATION_TOOLS:
            return None

        details = error.details if isinstance(error.details, dict) else {}
        if error.code == ToolErrorCode.AMBIGUOUS:
            candidates_raw = details.get("candidates")
            if not isinstance(candidates_raw, list):
                return None
            candidates: list[dict[str, str]] = []
            for candidate in candidates_raw:
                if not isinstance(candidate, dict):
                    continue
                raw_id = candidate.get("id")
                raw_name = candidate.get("name")
                if isinstance(raw_id, str) and isinstance(raw_name, str) and raw_name.strip():
                    candidates.append({"id": raw_id, "name": raw_name})
            if not candidates:
                return None
            candidate_names = ", ".join(candidate["name"] for candidate in candidates)
            return SetActiveTaskPlan(
                reply=(
                    f"Plusieurs comptes correspondent: {candidate_names}. "
                    "Répondez avec le nom exact (ou 1/2)."
                ),
                active_task={
                    "type": "select_bank_account",
                    "original_tool_name": plan.tool_name,
                    "original_payload": plan.payload,
                    "candidates": candidates,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )

        if error.code == ToolErrorCode.NOT_FOUND:
            close_names_raw = details.get("close_names")
            if not isinstance(close_names_raw, list):
                return None
            suggestions = [name.strip() for name in close_names_raw if isinstance(name, str) and name.strip()]
            if not suggestions:
                return None
            requested_name = details.get("name") if isinstance(details.get("name"), str) else plan.payload.get("name")
            requested_name_text = requested_name if isinstance(requested_name, str) and requested_name.strip() else ""
            suggestion_text = ", ".join(suggestions)
            return SetActiveTaskPlan(
                reply=(
                    f"Je ne trouve pas le compte « {requested_name_text} ». "
                    f"Vouliez-vous dire: {suggestion_text} ? "
                    "Répondez par le nom exact ou OUI pour choisir le premier."
                ),
                active_task={
                    "type": "select_bank_account",
                    "original_tool_name": plan.tool_name,
                    "original_payload": plan.payload,
                    "suggestions": suggestions,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        return None

    @staticmethod
    def _normalize_tool_result(tool_name: str, result: object) -> object:
        if result is not None:
            return result

        if tool_name in {"finance_categories_delete", "finance_bank_accounts_delete"}:
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
            if isinstance(result, ToolError):
                active_task_plan = self._build_bank_account_selection_active_task(plan, result)
                if active_task_plan is not None:
                    return AgentReply(
                        reply=active_task_plan.reply,
                        tool_result=self._serialize_tool_result(result),
                        plan={"tool_name": plan.tool_name, "payload": plan.payload},
                        active_task=active_task_plan.active_task,
                        should_update_active_task=True,
                    )
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
