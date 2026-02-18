"""Minimal deterministic agent loop (no LLM calls)."""

from __future__ import annotations

import logging
import hashlib
import unicodedata
from dataclasses import dataclass
from difflib import get_close_matches
from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel

from agent.answer_builder import build_final_reply
from agent.deterministic_nlu import parse_intent
from agent.llm_planner import LLMPlanner
from agent.planner import (
    ClarificationPlan,
    ErrorPlan,
    NoopPlan,
    Plan,
    SetActiveTaskPlan,
    ToolCallPlan,
    deterministic_plan_from_message,
    plan_from_message,
)
from agent.tool_router import ToolRouter
from shared import config
from shared.models import PROFILE_ALLOWED_FIELDS, RelevesGroupBy, ToolError, ToolErrorCode


logger = logging.getLogger(__name__)


_BANK_ACCOUNT_DISAMBIGUATION_TOOLS = {
    "finance_bank_accounts_delete",
    "finance_bank_accounts_set_default",
    "finance_bank_accounts_update",
}
_RISKY_WRITE_TOOLS = {
    "finance_bank_accounts_delete",
    "finance_categories_delete",
}
_SOFT_WRITE_TOOLS = {
    "finance_profile_update",
    "finance_categories_create",
    "finance_categories_update",
}
_CONFIRM_WORDS = {"oui", "o", "ok", "confirme", "confirmé", "confirmée"}
_REJECT_WORDS = {"non", "n", "annule", "annuler"}
_PROFILE_FIELD_ALIASES = {
    "ville": "city",
    "city": "city",
    "commune": "city",
    "localite": "city",
    "adresse ville": "city",
    "address city": "city",
    "pays": "country",
    "country": "country",
    "nation": "country",
    "code postal": "postal_code",
    "postal": "postal_code",
    "zipcode": "postal_code",
    "zip": "postal_code",
    "canton": "canton",
    "state": "canton",
    "adresse": "address_line1",
    "address": "address_line1",
    "address line1": "address_line1",
    "rue": "address_line1",
    "street": "address_line1",
    "adresse2": "address_line2",
    "address line2": "address_line2",
    "complement": "address_line2",
    "prenom": "first_name",
    "first name": "first_name",
    "nom": "last_name",
    "last name": "last_name",
    "date de naissance": "birth_date",
    "naissance": "birth_date",
    "birth date": "birth_date",
    "genre": "gender",
    "sexe": "gender",
    "gender": "gender",
}


def _wrap_write_plan_with_confirmation(plan: ToolCallPlan) -> SetActiveTaskPlan:
    return SetActiveTaskPlan(
        reply=(
            f"Je peux exécuter l'action « {plan.tool_name} ». "
            "Confirmez-vous ? (oui/non)"
        ),
        active_task={
            "type": "needs_confirmation",
            "confirmation_type": "confirm_llm_write",
            "context": {
                "tool_name": plan.tool_name,
                "payload": dict(plan.payload),
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def _build_clarification_tool_result(
    *,
    message: str,
    clarification_type: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "type": "clarification",
        "clarification_type": clarification_type,
        "message": message,
    }
    if isinstance(payload, dict) and payload:
        result["payload"] = payload
    return result


def _normalize_for_match(value: str) -> str:
    normalized = value.casefold().replace("-", " ").replace("_", " ")
    normalized = " ".join(normalized.split())
    without_accents = unicodedata.normalize("NFKD", normalized)
    return "".join(
        char for char in without_accents if not unicodedata.combining(char)
    ).strip()


def _normalize_profile_field_key(key: str) -> str:
    raw = key.strip()
    if raw and raw in PROFILE_ALLOWED_FIELDS:
        return raw

    normalized = _normalize_for_match(key)
    return _PROFILE_FIELD_ALIASES.get(normalized, normalized)


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
    shadow_llm: bool = False

    def _run_llm_shadow(
        self,
        message: str,
        *,
        profile_id: UUID | None,
        active_task: dict[str, object] | None,
        deterministic_plan: Plan,
    ) -> None:
        if self.llm_planner is None:
            return

        if not (self.shadow_llm or config.llm_shadow()):
            return

        message_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()[:12]

        try:
            llm_plan = plan_from_message(message, llm_planner=self.llm_planner)
        except Exception:
            logger.exception(
                "llm_shadow_plan_error",
                extra={
                    "message_hash": message_hash,
                    "profile_id": str(profile_id) if profile_id is not None else None,
                    "active_task_type": (
                        active_task.get("type")
                        if isinstance(active_task, dict)
                        and isinstance(active_task.get("type"), str)
                        else None
                    ),
                    "deterministic_plan_type": deterministic_plan.__class__.__name__,
                },
            )
            return

        deterministic_tool_name = (
            deterministic_plan.tool_name
            if isinstance(deterministic_plan, ToolCallPlan)
            else None
        )
        llm_tool_name = llm_plan.tool_name if isinstance(llm_plan, ToolCallPlan) else None
        same_tool = (
            isinstance(deterministic_plan, ToolCallPlan)
            and isinstance(llm_plan, ToolCallPlan)
            and deterministic_tool_name == llm_tool_name
        )

        logger.info(
            "llm_shadow_plan",
            extra={
                "message_hash": message_hash,
                "profile_id": str(profile_id) if profile_id is not None else None,
                "active_task_type": (
                    active_task.get("type")
                    if isinstance(active_task, dict)
                    and isinstance(active_task.get("type"), str)
                    else None
                ),
                "deterministic_plan_type": deterministic_plan.__class__.__name__,
                "deterministic_tool_name": deterministic_tool_name,
                "llm_plan_type": llm_plan.__class__.__name__,
                "llm_tool_name": llm_tool_name,
                "same_tool": same_tool,
            },
        )

    @staticmethod
    def plan_from_active_task(message: str, active_task: dict[str, object]):
        active_task_type = active_task.get("type")
        if active_task_type == "awaiting_search_merchant":
            merchant = message.strip().lower()
            date_range = active_task.get("date_range")
            clarification_payload = (
                {"date_range": date_range} if isinstance(date_range, dict) else None
            )
            if not merchant:
                return ClarificationPlan(
                    question="Que voulez-vous rechercher (ex: Migros, coffee, Coop) ?",
                    meta={
                        "keep_active_task": True,
                        "clarification_type": "awaiting_search_merchant",
                        "clarification_payload": clarification_payload,
                    },
                )

            payload: dict[str, object] = {
                "merchant": merchant,
                "limit": 50,
                "offset": 0,
            }
            if isinstance(date_range, dict):
                payload["date_range"] = date_range

            return ToolCallPlan(
                tool_name="finance_releves_search",
                payload=payload,
                user_reply="OK.",
                meta={"clear_active_task": True},
            )

        if active_task_type == "select_bank_account":
            return AgentLoop._plan_from_select_bank_account(
                message=message, active_task=active_task
            )

        if active_task_type == "needs_confirmation":
            confirmation_type = active_task.get("confirmation_type")
            context = active_task.get("context")
            if not isinstance(confirmation_type, str) or not isinstance(context, dict):
                return ClarificationPlan(
                    question="Je n'ai pas compris la tâche en attente.",
                    meta={"keep_active_task": True},
                )
            return AgentLoop._plan_from_needs_confirmation(
                message=message,
                confirmation_type=confirmation_type,
                context=context,
            )

        if active_task_type not in {
            "confirm_delete_category",
            "confirm_delete_bank_account",
        }:
            return ClarificationPlan(
                question="Je n'ai pas compris la tâche en attente.",
                meta={"keep_active_task": True},
            )

        context: dict[str, object] = {}
        if active_task_type == "confirm_delete_category":
            context["category_name"] = active_task.get("category_name")
        if active_task_type == "confirm_delete_bank_account":
            context["name"] = active_task.get("name")
            context["bank_account_id"] = active_task.get("bank_account_id")
        return AgentLoop._plan_from_needs_confirmation(
            message=message,
            confirmation_type=str(active_task_type),
            context=context,
        )

    @staticmethod
    def _plan_from_needs_confirmation(
        *,
        message: str,
        confirmation_type: str,
        context: dict[str, object],
    ):
        normalized = message.strip().lower()
        if not normalized:
            return ClarificationPlan(
                question="Répondez OUI ou NON.", meta={"keep_active_task": True}
            )

        if normalized in _REJECT_WORDS:
            if confirmation_type == "confirm_llm_write":
                return NoopPlan(reply="Action annulée.", meta={"clear_active_task": True})
            return NoopPlan(
                reply="Suppression annulée.", meta={"clear_active_task": True}
            )

        if normalized not in _CONFIRM_WORDS:
            return ClarificationPlan(
                question="Répondez OUI ou NON.", meta={"keep_active_task": True}
            )

        if confirmation_type == "confirm_delete_category":
            target_name = str(context.get("category_name", "")).strip()
            if not target_name:
                return NoopPlan(
                    reply="Suppression annulée.", meta={"clear_active_task": True}
                )
            return ToolCallPlan(
                tool_name="finance_categories_delete",
                payload={"category_name": target_name},
                user_reply="Catégorie supprimée.",
                meta={"clear_active_task": True},
            )

        if confirmation_type == "confirm_delete_bank_account":
            target_name = str(context.get("name", "")).strip()
            if not target_name:
                return NoopPlan(
                    reply="Suppression annulée.", meta={"clear_active_task": True}
                )
            bank_account_id = context.get("bank_account_id")
            payload = (
                {"bank_account_id": bank_account_id.strip()}
                if isinstance(bank_account_id, str) and bank_account_id.strip()
                else {"name": target_name}
            )
            return ToolCallPlan(
                tool_name="finance_bank_accounts_delete",
                payload=payload,
                user_reply="Compte supprimé.",
                meta={"clear_active_task": True},
            )

        if confirmation_type == "confirm_llm_write":
            tool_name = context.get("tool_name")
            payload = context.get("payload")
            if (
                not isinstance(tool_name, str)
                or not tool_name.strip()
                or tool_name not in _RISKY_WRITE_TOOLS
                or not isinstance(payload, dict)
            ):
                return NoopPlan(reply="Action annulée.", meta={"clear_active_task": True})

            return ToolCallPlan(
                tool_name=tool_name,
                payload=payload,
                user_reply="OK.",
                meta={"clear_active_task": True},
            )

        return ClarificationPlan(
            question="Je n'ai pas compris la tâche en attente.",
            meta={"keep_active_task": True},
        )

    @staticmethod
    def _plan_from_select_bank_account(message: str, active_task: dict[str, object]):
        original_tool_name = active_task.get("original_tool_name")
        if (
            not isinstance(original_tool_name, str)
            or original_tool_name not in _BANK_ACCOUNT_DISAMBIGUATION_TOOLS
        ):
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
                if (
                    isinstance(raw_id, str)
                    and isinstance(raw_name, str)
                    and raw_name.strip()
                ):
                    candidates.append({"id": raw_id, "name": raw_name})

        suggestions: list[str] = []
        if isinstance(suggestions_raw, list):
            suggestions = [
                name.strip()
                for name in suggestions_raw
                if isinstance(name, str) and name.strip()
            ]

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
            payload: dict[str, object] = (
                {"set": set_payload} if isinstance(set_payload, dict) else {}
            )
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
    def _build_bank_account_selection_active_task(
        plan: ToolCallPlan, error: ToolError
    ) -> SetActiveTaskPlan | None:
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
                if (
                    isinstance(raw_id, str)
                    and isinstance(raw_name, str)
                    and raw_name.strip()
                ):
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
            suggestions = [
                name.strip()
                for name in close_names_raw
                if isinstance(name, str) and name.strip()
            ]
            if not suggestions:
                return None
            requested_name = (
                details.get("name")
                if isinstance(details.get("name"), str)
                else plan.payload.get("name")
            )
            requested_name_text = (
                requested_name
                if isinstance(requested_name, str) and requested_name.strip()
                else ""
            )
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
    def _resolve_delete_bank_account_confirmation(
        plan: SetActiveTaskPlan,
        *,
        tool_router: ToolRouter,
        profile_id: UUID,
    ) -> AgentReply | None:
        active_task = plan.active_task if isinstance(plan.active_task, dict) else {}
        raw_name: object | None = None
        if active_task.get("type") == "confirm_delete_bank_account":
            raw_name = active_task.get("name")
        elif (
            active_task.get("type") == "needs_confirmation"
            and active_task.get("confirmation_type") == "confirm_delete_bank_account"
        ):
            context = active_task.get("context")
            if isinstance(context, dict):
                raw_name = context.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            return None

        requested_name = raw_name.strip()
        list_plan = ToolCallPlan(
            tool_name="finance_bank_accounts_list",
            payload={},
            user_reply="",
        )
        list_result = tool_router.call(
            "finance_bank_accounts_list", {}, profile_id=profile_id
        )
        normalized_list_result = AgentLoop._normalize_tool_result(
            "finance_bank_accounts_list", list_result
        )
        if isinstance(normalized_list_result, ToolError):
            return AgentReply(
                reply=build_final_reply(
                    plan=list_plan, tool_result=normalized_list_result
                ),
                tool_result=AgentLoop._serialize_tool_result(normalized_list_result),
                plan={"tool_name": list_plan.tool_name, "payload": list_plan.payload},
            )

        items = getattr(normalized_list_result, "items", None)
        if not isinstance(items, list):
            return None

        target = requested_name.lower()
        exact_matches = [
            account
            for account in items
            if isinstance(getattr(account, "name", None), str)
            and account.name.strip().lower() == target
        ]

        if len(exact_matches) == 0:
            names_by_norm: dict[str, str] = {}
            for account in items:
                if not isinstance(getattr(account, "name", None), str):
                    continue
                normalized_name = account.name.strip().lower()
                if normalized_name and normalized_name not in names_by_norm:
                    names_by_norm[normalized_name] = account.name
            suggestions = [
                names_by_norm[name_norm]
                for name_norm in get_close_matches(
                    target, list(names_by_norm.keys()), n=3, cutoff=0.6
                )
            ]
            error = ToolError(
                code=ToolErrorCode.NOT_FOUND,
                message="Bank account not found for provided name.",
                details={"name": requested_name, "close_names": suggestions},
            )
            delete_plan = ToolCallPlan(
                tool_name="finance_bank_accounts_delete",
                payload={"name": requested_name},
                user_reply="",
            )
            return AgentReply(
                reply=build_final_reply(plan=delete_plan, tool_result=error),
                tool_result=AgentLoop._serialize_tool_result(error),
                plan={
                    "tool_name": delete_plan.tool_name,
                    "payload": delete_plan.payload,
                },
                active_task=None,
                should_update_active_task=True,
            )

        if len(exact_matches) == 1:
            account = exact_matches[0]
            can_delete_result = tool_router.call(
                "finance_bank_accounts_can_delete",
                {"bank_account_id": str(account.id)},
                profile_id=profile_id,
            )
            normalized_can_delete = AgentLoop._normalize_tool_result(
                "finance_bank_accounts_can_delete",
                can_delete_result,
            )
            if isinstance(normalized_can_delete, ToolError):
                can_delete_plan = ToolCallPlan(
                    tool_name="finance_bank_accounts_can_delete",
                    payload={"bank_account_id": str(account.id)},
                    user_reply="",
                )
                return AgentReply(
                    reply=build_final_reply(
                        plan=can_delete_plan, tool_result=normalized_can_delete
                    ),
                    tool_result=AgentLoop._serialize_tool_result(normalized_can_delete),
                    plan={
                        "tool_name": can_delete_plan.tool_name,
                        "payload": can_delete_plan.payload,
                    },
                )

            if (
                isinstance(normalized_can_delete, dict)
                and normalized_can_delete.get("ok") is True
                and normalized_can_delete.get("can_delete") is False
            ):
                error = ToolError(
                    code=ToolErrorCode.CONFLICT, message="bank account not empty"
                )
                delete_plan = ToolCallPlan(
                    tool_name="finance_bank_accounts_delete",
                    payload={"bank_account_id": str(account.id)},
                    user_reply="",
                )
                return AgentReply(
                    reply=build_final_reply(plan=delete_plan, tool_result=error),
                    tool_result=AgentLoop._serialize_tool_result(error),
                    plan={
                        "tool_name": delete_plan.tool_name,
                        "payload": delete_plan.payload,
                    },
                    active_task=None,
                    should_update_active_task=True,
                )

            return AgentReply(
                reply=(
                    f"Confirmez-vous la suppression du compte « {account.name} » ? "
                    "Répondez OUI ou NON."
                ),
                active_task={
                    "type": "needs_confirmation",
                    "confirmation_type": "confirm_delete_bank_account",
                    "context": {
                        "bank_account_id": str(account.id),
                        "name": account.name,
                    },
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                should_update_active_task=True,
            )

        candidate_names = ", ".join(account.name for account in exact_matches)
        return AgentReply(
            reply=f"Plusieurs comptes correspondent: {candidate_names}.",
            active_task=None,
            should_update_active_task=True,
        )

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

    @staticmethod
    def _validate_llm_tool_payload(
        tool_name: str,
        payload: dict[str, object],
    ) -> tuple[bool, str | None, dict[str, object]]:
        """Validate and normalize gated LLM tool payloads."""
        if tool_name == "finance_releves_search":
            merchant = payload.get("merchant")
            if not isinstance(merchant, str) or not merchant.strip():
                return False, "missing_merchant", payload
            normalized_payload = dict(payload)
            normalized_payload["merchant"] = merchant.strip()
            normalized_payload.setdefault("limit", 50)
            normalized_payload.setdefault("offset", 0)
            return True, None, normalized_payload

        if tool_name == "finance_bank_accounts_list":
            return True, None, {}

        if tool_name == "finance_categories_list":
            return True, None, {}

        if tool_name == "finance_categories_delete":
            category_name = payload.get("category_name")
            if not isinstance(category_name, str) or not category_name.strip():
                return False, "missing_category_name", payload
            return True, None, {"category_name": category_name.strip()}

        if tool_name == "finance_profile_update":
            raw_set = payload.get("set")
            if not isinstance(raw_set, dict) or not raw_set:
                return False, "invalid_profile_update", payload

            normalized_set: dict[str, object] = {}
            for field_name, field_value in raw_set.items():
                if not isinstance(field_name, str):
                    return False, "invalid_profile_update", payload

                normalized_field = _normalize_profile_field_key(field_name)
                if normalized_field not in PROFILE_ALLOWED_FIELDS:
                    return False, "invalid_profile_update_field", payload

                if isinstance(field_value, str):
                    stripped_value = field_value.strip()
                    if not stripped_value:
                        continue
                    normalized_set[normalized_field] = stripped_value
                    continue

                normalized_set[normalized_field] = field_value

            if not normalized_set:
                return False, "invalid_profile_update", payload

            return True, None, {"set": normalized_set}

        if tool_name == "finance_profile_get":
            fields = payload.get("fields")
            if not isinstance(fields, list) or not fields:
                return False, "missing_fields", payload
            normalized_fields: list[str] = []
            for field_name in fields:
                if not isinstance(field_name, str):
                    return False, "invalid_fields", payload
                normalized_field = _normalize_profile_field_key(field_name)
                if normalized_field not in PROFILE_ALLOWED_FIELDS:
                    return False, "invalid_fields", payload
                normalized_fields.append(normalized_field)

            normalized_payload = dict(payload)
            normalized_payload["fields"] = normalized_fields
            return True, None, normalized_payload

        if tool_name == "finance_releves_sum":
            direction = payload.get("direction")
            has_filters = any(
                key in payload for key in ("merchant", "search", "date_range", "filters")
            )
            if direction in {"DEBIT_ONLY", "CREDIT_ONLY"} or has_filters:
                return True, None, dict(payload)
            return False, "missing_filters_or_direction", payload

        if tool_name == "finance_releves_aggregate":
            group_by = payload.get("group_by")
            if not isinstance(group_by, str) or not group_by.strip():
                return False, "missing_group_by", payload

            normalized_group_by = group_by.strip()
            allowed_group_by = {enum_member.value for enum_member in RelevesGroupBy}
            if normalized_group_by not in allowed_group_by:
                return False, "invalid_group_by", payload

            normalized_payload = dict(payload)
            normalized_payload["group_by"] = normalized_group_by

            direction = payload.get("direction")
            if direction is not None:
                if not isinstance(direction, str) or not direction.strip():
                    return False, "invalid_direction", payload
                normalized_direction = direction.strip()
                if normalized_direction not in {"DEBIT_ONLY", "CREDIT_ONLY", "ALL"}:
                    return False, "invalid_direction", payload
                normalized_payload["direction"] = normalized_direction

            date_range = payload.get("date_range")
            if date_range is not None:
                if not isinstance(date_range, dict):
                    return False, "invalid_date_range", payload

                start_date = date_range.get("start_date")
                end_date = date_range.get("end_date")
                if (
                    not isinstance(start_date, str)
                    or not start_date.strip()
                    or not isinstance(end_date, str)
                    or not end_date.strip()
                ):
                    return False, "invalid_date_range", payload

                normalized_payload["date_range"] = {
                    "start_date": start_date.strip(),
                    "end_date": end_date.strip(),
                }

            return True, None, normalized_payload

        return True, None, dict(payload)

    def handle_user_message(
        self,
        message: str,
        *,
        profile_id: UUID | None = None,
        active_task: dict[str, object] | None = None,
    ) -> AgentReply:
        routed = self._route_message(
            message,
            profile_id=profile_id,
            active_task=active_task,
        )
        if isinstance(routed, AgentReply):
            return routed
        plan = routed
        self._run_llm_shadow(
            message,
            profile_id=profile_id,
            active_task=active_task,
            deterministic_plan=plan,
        )

        plan_meta = (
            getattr(plan, "meta", {})
            if isinstance(getattr(plan, "meta", {}), dict)
            else {}
        )
        should_update_active_task = False
        updated_active_task = active_task
        if plan_meta.get("clear_active_task"):
            should_update_active_task = True
            updated_active_task = None
        elif plan_meta.get("keep_active_task"):
            should_update_active_task = True
            updated_active_task = active_task
            clarification_type = plan_meta.get("clarification_type")
            clarification_payload = plan_meta.get("clarification_payload")
            if active_task is None and clarification_type == "awaiting_search_merchant":
                updated_active_task = {"type": "awaiting_search_merchant"}
                if isinstance(clarification_payload, dict):
                    date_range = clarification_payload.get("date_range")
                    if isinstance(date_range, dict):
                        updated_active_task["date_range"] = date_range

        if isinstance(plan, SetActiveTaskPlan):
            if profile_id is not None:
                resolved_reply = self._resolve_delete_bank_account_confirmation(
                    plan,
                    tool_router=self.tool_router,
                    profile_id=profile_id,
                )
                if resolved_reply is not None:
                    return resolved_reply
            return AgentReply(
                reply=plan.reply,
                active_task=plan.active_task,
                should_update_active_task=True,
            )

        if (
            isinstance(plan, ToolCallPlan)
            and plan.tool_name in _RISKY_WRITE_TOOLS
            and active_task is None
        ):
            confirmation_plan = _wrap_write_plan_with_confirmation(plan)
            return AgentReply(
                reply=confirmation_plan.reply,
                active_task=confirmation_plan.active_task,
                should_update_active_task=True,
            )

        if isinstance(plan, ToolCallPlan):
            if (
                plan.tool_name == "finance_releves_search"
                and profile_id is not None
                and isinstance(plan.meta.get("bank_account_hint"), str)
            ):
                bank_account_hint = str(plan.meta["bank_account_hint"])
                list_result = self.tool_router.call(
                    "finance_bank_accounts_list", {}, profile_id=profile_id
                )
                normalized_list_result = self._normalize_tool_result(
                    "finance_bank_accounts_list", list_result
                )
                if not isinstance(normalized_list_result, ToolError):
                    items = getattr(normalized_list_result, "items", None)
                    if isinstance(items, list):
                        matched_account_id: str | None = None
                        for account in items:
                            raw_name = getattr(account, "name", None)
                            raw_id = getattr(account, "id", None)
                            if not isinstance(raw_name, str) or raw_id is None:
                                continue
                            if _normalize_for_match(raw_name) == _normalize_for_match(
                                bank_account_hint
                            ):
                                matched_account_id = str(raw_id)
                                break
                        if matched_account_id is not None:
                            plan.payload["bank_account_id"] = matched_account_id
                        else:
                            merchant_fallback = plan.meta.get("merchant_fallback")
                            if (
                                isinstance(merchant_fallback, str)
                                and merchant_fallback.strip()
                            ):
                                plan.payload["merchant"] = (
                                    merchant_fallback.strip().casefold()
                                )

            logger.info("tool_execution_started tool_name=%s", plan.tool_name)
            raw_result = self.tool_router.call(
                plan.tool_name, plan.payload, profile_id=profile_id
            )
            result = self._normalize_tool_result(plan.tool_name, raw_result)
            if isinstance(result, ToolError):
                active_task_plan = self._build_bank_account_selection_active_task(
                    plan, result
                )
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
            clarification_type = plan_meta.get("clarification_type")
            clarification_payload = plan_meta.get("clarification_payload")
            return AgentReply(
                reply=plan.question,
                tool_result=_build_clarification_tool_result(
                    message=plan.question,
                    clarification_type=(
                        clarification_type
                        if isinstance(clarification_type, str) and clarification_type
                        else "generic"
                    ),
                    payload=(
                        clarification_payload
                        if isinstance(clarification_payload, dict)
                        else None
                    ),
                ),
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

    def _route_message(
        self,
        message: str,
        *,
        profile_id: UUID | None,
        active_task: dict[str, object] | None,
    ) -> Plan | AgentReply:
        if active_task is not None:
            return self.plan_from_active_task(message, active_task)

        nlu_intent = parse_intent(message)
        if isinstance(nlu_intent, dict):
            intent_type = nlu_intent.get("type")
            if intent_type == "clarification":
                clarification_message = nlu_intent.get("message")
                if isinstance(clarification_message, str):
                    clarification_type = nlu_intent.get("clarification_type")
                    if clarification_type == "awaiting_search_merchant":
                        clarification_payload: dict[str, object] = {}
                        date_range = nlu_intent.get("date_range")
                        if isinstance(date_range, dict):
                            clarification_payload["date_range"] = date_range
                        return ClarificationPlan(
                            question=clarification_message,
                            meta={
                                "keep_active_task": True,
                                "clarification_type": "awaiting_search_merchant",
                                "clarification_payload": clarification_payload,
                            },
                        )

                    return ClarificationPlan(
                        question=clarification_message,
                        meta={
                            "clarification_type": (
                                str(clarification_type)
                                if isinstance(clarification_type, str)
                                and clarification_type
                                else "generic"
                            )
                        },
                    )

            if intent_type == "ui_action":
                action = nlu_intent.get("action")
                if action == "open_import_panel":
                    return AgentReply(
                        reply="D'accord, j'ouvre le panneau d'import de relevés.",
                        tool_result={
                            "type": "ui_action",
                            "action": "open_import_panel",
                        },
                    )

            if intent_type == "tool_call":
                tool_name = nlu_intent.get("tool_name")
                payload = nlu_intent.get("payload")
                if isinstance(tool_name, str) and isinstance(payload, dict):
                    meta: dict[str, object] = {}
                    bank_account_hint = nlu_intent.get("bank_account_hint")
                    if isinstance(bank_account_hint, str) and bank_account_hint.strip():
                        meta["bank_account_hint"] = bank_account_hint.strip().casefold()
                    merchant_fallback = nlu_intent.get("merchant_fallback")
                    if isinstance(merchant_fallback, str) and merchant_fallback.strip():
                        meta["merchant_fallback"] = (
                            merchant_fallback.strip().casefold()
                        )
                    return ToolCallPlan(
                        tool_name=tool_name,
                        payload=payload,
                        user_reply="OK.",
                        meta=meta,
                    )

            if intent_type == "needs_confirmation":
                confirmation_type = nlu_intent.get("confirmation_type")
                context = nlu_intent.get("context")
                reply_message = nlu_intent.get("message")
                if isinstance(confirmation_type, str) and isinstance(context, dict):
                    if confirmation_type == "confirm_llm_write":
                        tool_name = context.get("tool_name")
                        payload = context.get("payload")
                        if (
                            isinstance(tool_name, str)
                            and tool_name in _SOFT_WRITE_TOOLS
                            and isinstance(payload, dict)
                        ):
                            return ToolCallPlan(
                                tool_name=tool_name,
                                payload=payload,
                                user_reply="OK.",
                            )

                    if isinstance(reply_message, str) and reply_message.strip():
                        return SetActiveTaskPlan(
                            reply=reply_message,
                            active_task={
                                "type": "needs_confirmation",
                                "confirmation_type": confirmation_type,
                                "context": context,
                                "created_at": datetime.now(timezone.utc).isoformat(),
                            },
                        )

        deterministic_plan = deterministic_plan_from_message(message)
        if isinstance(
            deterministic_plan,
            (ToolCallPlan, ErrorPlan, ClarificationPlan, SetActiveTaskPlan),
        ):
            return deterministic_plan
        if (
            isinstance(deterministic_plan, NoopPlan)
            and deterministic_plan.reply == "pong"
        ):
            return deterministic_plan
        if self.llm_planner is None:
            return deterministic_plan

        if not config.llm_enabled():
            return deterministic_plan

        if not config.llm_gated():
            return plan_from_message(message, llm_planner=self.llm_planner)

        message_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()[:12]
        if not isinstance(deterministic_plan, NoopPlan):
            return deterministic_plan

        try:
            llm_plan = plan_from_message(message, llm_planner=self.llm_planner)
        except Exception:
            logger.exception(
                "llm_gated_error",
                extra={
                    "event": "llm_gated_error",
                    "message_hash": message_hash,
                    "profile_id": str(profile_id) if profile_id is not None else None,
                    "deterministic_plan_type": deterministic_plan.__class__.__name__,
                },
            )
            return deterministic_plan

        logger.info(
            "llm_gated_used",
            extra={
                "event": "llm_gated_used",
                "message_hash": message_hash,
                "profile_id": str(profile_id) if profile_id is not None else None,
                "deterministic_plan_type": deterministic_plan.__class__.__name__,
                "llm_plan_type": llm_plan.__class__.__name__,
            },
        )
        if isinstance(llm_plan, ToolCallPlan):
            allowed_tools = config.llm_allowed_tools()
            if llm_plan.tool_name not in allowed_tools:
                logger.info(
                    "llm_tool_blocked",
                    extra={
                        "event": "llm_tool_blocked",
                        "message_hash": message_hash,
                        "profile_id": str(profile_id) if profile_id is not None else None,
                        "tool_name": llm_plan.tool_name,
                        "allowlist": sorted(allowed_tools),
                    },
                )
                return deterministic_plan

            is_valid, reason, normalized_payload = self._validate_llm_tool_payload(
                llm_plan.tool_name,
                llm_plan.payload,
            )
            if not is_valid:
                logger.info(
                    "llm_payload_invalid",
                    extra={
                        "event": "llm_payload_invalid",
                        "message_hash": message_hash,
                        "profile_id": str(profile_id) if profile_id is not None else None,
                        "tool_name": llm_plan.tool_name,
                        "reason": reason,
                    },
                )
                return deterministic_plan

            logger.info(
                "llm_tool_allowed",
                extra={
                    "event": "llm_tool_allowed",
                    "message_hash": message_hash,
                    "profile_id": str(profile_id) if profile_id is not None else None,
                    "tool_name": llm_plan.tool_name,
                    "same_as_deterministic": False,
                    "payload_keys": sorted(normalized_payload.keys()),
                },
            )

            if llm_plan.tool_name in _RISKY_WRITE_TOOLS:
                logger.info(
                    "llm_tool_requires_confirmation",
                    extra={
                        "event": "llm_tool_requires_confirmation",
                        "message_hash": message_hash,
                        "profile_id": str(profile_id) if profile_id is not None else None,
                        "tool_name": llm_plan.tool_name,
                    },
                )
                return SetActiveTaskPlan(
                    reply=(
                        f"Je peux exécuter l'action « {llm_plan.tool_name} ». "
                        "Confirmez-vous ? (oui/non)"
                    ),
                    active_task={
                        "type": "needs_confirmation",
                        "confirmation_type": "confirm_llm_write",
                        "context": {
                            "tool_name": llm_plan.tool_name,
                            "payload": normalized_payload,
                        },
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                )

            return ToolCallPlan(
                tool_name=llm_plan.tool_name,
                payload=normalized_payload,
                user_reply=llm_plan.user_reply,
                meta=dict(llm_plan.meta),
            )

        return deterministic_plan
