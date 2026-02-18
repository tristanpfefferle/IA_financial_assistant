"""Minimal deterministic agent loop (no LLM calls)."""

from __future__ import annotations

import logging
import re
import hashlib
import unicodedata
from dataclasses import dataclass
from difflib import get_close_matches
from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel

from agent.answer_builder import build_final_reply
from agent.deterministic_nlu import parse_intent
from agent.llm_judge import LLMJudge
from agent.llm_planner import LLMPlanner
from agent.memory import (
    QueryMemory,
    apply_memory_to_plan,
    extract_memory_from_plan,
    followup_plan_from_message,
    is_followup_message,
    period_payload_from_message,
)
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
from shared.models import (
    PROFILE_ALLOWED_FIELDS,
    RelevesFilters,
    RelevesGroupBy,
    ToolError,
    ToolErrorCode,
)


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
_WRITE_TOOLS = _RISKY_WRITE_TOOLS | _SOFT_WRITE_TOOLS
_CONFIRM_WORDS = {"oui", "o", "ok", "confirme", "confirmé", "confirmée"}
_REJECT_WORDS = {"non", "n", "annule", "annuler"}
_DIRECTION_DEBIT_WORDS = {"depenses", "dépenses", "depense", "dépense", "debit"}
_DIRECTION_CREDIT_WORDS = {"revenus", "revenu", "credit"}
_DIRECTION_BOTH_WORDS = {"les deux", "both", "deux"}
_ACTIVE_TASK_TTL_SECONDS = 600
_NEW_REQUEST_INTENT_WORDS = {
    "total",
    "totaux",
    "transaction",
    "transactions",
    "depense",
    "depenses",
    "dépense",
    "dépenses",
    "revenu",
    "revenus",
    "solde",
    "recherche",
    "chercher",
}
_CONFIDENCE_SHORT_FOLLOWUP_PATTERN = re.compile(r"^(et\s+en|et|ok|pareil|idem)\b", re.IGNORECASE)
_CONFIDENCE_EXPLICIT_DATE_PATTERN = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b",
    re.IGNORECASE,
)
_CONFIDENCE_YEAR_PATTERN = re.compile(r"\b(?:19|20)\d{2}\b")
_CONFIDENCE_AMBIGUOUS_TIME_REFERENCES = (
    "mois suivant",
    "mois d'apres",
    "mois d'après",
)
_CONFIDENCE_MONTH_TOKENS = (
    "janvier",
    "fevrier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "aout",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "decembre",
    "décembre",
)
_CONFIDENCE_MONTH_TO_NUMBER = {
    "janvier": 1,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
}
_FILTER_PREFIXES_TO_STRIP = (
    "le ",
    "la ",
    "les ",
    "un ",
    "une ",
    "du ",
    "des ",
    "de ",
    "d'",
)
_FOLLOWUP_WRITE_PREVENTION_PREFIX_PATTERN = re.compile(
    r"^(?:ok|et|pareil|idem)\b[\s,:;\-]*",
    re.IGNORECASE,
)
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


def _canonicalize_category(
    payload: dict[str, object],
    known_categories: list[str],
) -> None:
    raw_category = payload.get("categorie")
    if not isinstance(raw_category, str) or not raw_category.strip():
        return

    normalized = _normalize_for_match(raw_category)
    for category_name in known_categories:
        if _normalize_for_match(category_name) == normalized:
            payload["categorie"] = category_name
            break


def _normalize_profile_field_key(key: str) -> str:
    raw = key.strip()
    if raw and raw in PROFILE_ALLOWED_FIELDS:
        return raw

    normalized = _normalize_for_match(key)
    return _PROFILE_FIELD_ALIASES.get(normalized, normalized)


def _strip_filter_prefixes(value: str) -> str:
    normalized = _normalize_for_match(value)
    for prefix in _FILTER_PREFIXES_TO_STRIP:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :].strip()
            break
    return normalized


def _cleanup_explicit_filter_value(value: str) -> str:
    cleaned = _strip_filter_prefixes(value)
    month_tokens_pattern = "|".join(_CONFIDENCE_MONTH_TO_NUMBER.keys())
    cleaned = re.sub(
        rf"\s+en\s+(?:{month_tokens_pattern})\b.*$",
        "",
        cleaned,
    ).strip()
    return cleaned


def _extract_followup_focus_for_write_prevention(message: str) -> str | None:
    focus = message.strip().rstrip(" ?.!…")
    focus = _FOLLOWUP_WRITE_PREVENTION_PREFIX_PATTERN.sub("", focus, count=1).strip()
    if not focus:
        return None
    if len(focus) > 40:
        return f"{focus[:40]}…"
    return focus


def _write_prevention_choice_from_message(message: str) -> str | None:
    normalized = _normalize_for_match(message)
    if not normalized:
        return None

    merchant_tokens = {"marchand", "merchant", "payee", "commercant", "commerçant"}
    category_tokens = {"categorie", "category", "cat"}
    tokens = set(normalized.split())

    if tokens & merchant_tokens:
        return "merchant"
    if tokens & category_tokens:
        return "category"
    return None


def _merchant_vs_keyword_choice_from_message(
    message: str,
    *,
    merchant: str,
    keyword: str,
) -> str | None:
    normalized = _normalize_for_match(message)
    if not normalized:
        return None

    tokens = set(normalized.split())
    compact = normalized.replace(" ", "")
    merchant_tokens = {"marchand", "merchant", "payee"}
    keyword_tokens = {"motcle", "motclef", "keyword"}

    merchant_match = bool(tokens & merchant_tokens)
    keyword_match = bool(tokens & keyword_tokens)
    if compact in keyword_tokens:
        keyword_match = True

    normalized_merchant = _normalize_for_match(merchant)
    normalized_keyword = _normalize_for_match(keyword)
    if normalized_merchant and normalized_merchant in normalized:
        merchant_match = True
    if normalized_keyword and normalized_keyword in normalized:
        keyword_match = True
    if normalized_keyword and normalized_keyword.replace(" ", "") in compact:
        keyword_match = True

    if merchant_match and keyword_match:
        return None
    if merchant_match:
        return "merchant"
    if keyword_match:
        return "keyword"
    return None


def _date_range_from_pending_context(
    context: dict[str, object],
    query_memory: QueryMemory | None,
) -> dict[str, str] | None:
    period_payload = context.get("period_payload")
    if isinstance(period_payload, dict):
        date_range = period_payload.get("date_range")
        if isinstance(date_range, dict):
            start_date = date_range.get("start_date")
            end_date = date_range.get("end_date")
            if isinstance(start_date, str) and isinstance(end_date, str):
                return {"start_date": start_date, "end_date": end_date}

    if isinstance(query_memory, QueryMemory) and isinstance(query_memory.date_range, dict):
        start_date = query_memory.date_range.get("start_date")
        end_date = query_memory.date_range.get("end_date")
        if isinstance(start_date, str) and isinstance(end_date, str):
            return {"start_date": start_date, "end_date": end_date}
    return None


@dataclass(slots=True)
class AgentReply:
    """Serializable chat output for API responses."""

    reply: str
    tool_result: dict[str, object] | None = None
    plan: dict[str, object] | None = None
    active_task: dict[str, object] | None = None
    should_update_active_task: bool = False
    memory_update: dict[str, object] | None = None


@dataclass(slots=True)
class AgentLoop:
    tool_router: ToolRouter
    llm_planner: LLMPlanner | None = None
    llm_judge: LLMJudge | None = None
    shadow_llm: bool = False

    @staticmethod
    def _with_confidence_meta(
        message: str,
        plan: ToolCallPlan,
        *,
        query_memory: QueryMemory | None,
    ) -> ToolCallPlan:
        confidence = "high"
        reasons: list[str] = []
        stripped_message = message.strip()
        normalized_message = stripped_message.lower()
        normalized_message_no_accents = _normalize_for_match(stripped_message)
        payload = plan.payload
        is_releves_query_tool = plan.tool_name in {
            "finance_releves_sum",
            "finance_releves_search",
            "finance_releves_aggregate",
        }
        has_explicit_intent = any(
            keyword in normalized_message
            for keyword in (
                "dépense",
                "depense",
                "revenu",
                "recherche",
                "cherche",
                "search",
                "somme",
                "total",
            )
        )
        has_explicit_filter = any(
            token in normalized_message
            for token in ("chez", "catégorie", "categorie", "merchant", "marchand")
        )
        has_explicit_year = bool(_CONFIDENCE_YEAR_PATTERN.search(normalized_message))
        has_explicit_month = any(month in normalized_message for month in _CONFIDENCE_MONTH_TOKENS)
        has_explicit_date_literal = bool(_CONFIDENCE_EXPLICIT_DATE_PATTERN.search(normalized_message))
        has_explicit_period = (has_explicit_month and has_explicit_year) or has_explicit_date_literal
        has_ambiguous_time_reference = any(
            token in normalized_message_no_accents
            for token in _CONFIDENCE_AMBIGUOUS_TIME_REFERENCES
        )
        has_plan_date_range = isinstance(payload.get("date_range"), dict)
        memory_reason = str(plan.meta.get("memory_reason", ""))
        period_injected_from_memory = "period_from_memory" in memory_reason
        period_came_from_memory = bool(plan.meta.get("followup_from_memory")) or period_injected_from_memory
        is_regex_followup = bool(
            _CONFIDENCE_SHORT_FOLLOWUP_PATTERN.match(normalized_message)
        )
        is_short_followup = len(stripped_message) <= 25
        category_value = str(payload.get("categorie", "")).strip()
        merchant_value = str(payload.get("merchant", "")).strip()
        has_category_value_in_message = bool(category_value) and (
            _normalize_for_match(category_value) in normalized_message_no_accents
        )
        has_merchant_value_in_message = bool(merchant_value) and (
            _normalize_for_match(merchant_value) in normalized_message_no_accents
        )
        has_payload_filter_value_in_message = (
            has_category_value_in_message or has_merchant_value_in_message
        )

        merchant_match = re.search(
            r"\b(?:chez|merchant|marchand)\s+([^?!.;,]+)",
            normalized_message_no_accents,
        )
        explicit_merchant_in_message = (
            _cleanup_explicit_filter_value(merchant_match.group(1).strip())
            if merchant_match is not None
            else ""
        )
        merchant_conflict = (
            bool(merchant_value)
            and bool(explicit_merchant_in_message)
            and _cleanup_explicit_filter_value(merchant_value) != explicit_merchant_in_message
        )

        category_match = re.search(
            r"\bcat[eé]gorie\s+([^?!.;,]+)",
            normalized_message_no_accents,
        )
        explicit_category_in_message = (
            _cleanup_explicit_filter_value(category_match.group(1).strip())
            if category_match is not None
            else ""
        )
        category_conflict = (
            bool(category_value)
            and bool(explicit_category_in_message)
            and _cleanup_explicit_filter_value(category_value) != explicit_category_in_message
        )

        period_conflict = False
        date_range = payload.get("date_range")
        if (
            has_explicit_month
            and has_explicit_year
            and isinstance(date_range, dict)
            and isinstance(date_range.get("start_date"), str)
        ):
            start_date = str(date_range["start_date"])
            payload_parts = start_date.split("-")
            if len(payload_parts) == 3 and all(part.isdigit() for part in payload_parts):
                payload_year = int(payload_parts[0])
                payload_month = int(payload_parts[1])
                year_match = _CONFIDENCE_YEAR_PATTERN.search(normalized_message_no_accents)
                month_token = next(
                    (
                        token
                        for token in _CONFIDENCE_MONTH_TO_NUMBER
                        if token in normalized_message_no_accents
                    ),
                    None,
                )
                if year_match is not None and month_token is not None:
                    requested_year = int(year_match.group(0))
                    requested_month = _CONFIDENCE_MONTH_TO_NUMBER[month_token]
                    period_conflict = (payload_year, payload_month) != (
                        requested_year,
                        requested_month,
                    )
        if period_conflict:
            confidence = "low"
            reasons.append("period_conflict")

        followup_short_detected = (
            is_releves_query_tool
            and is_followup_message(message)
            and (
                is_regex_followup
                or (
                    is_short_followup
                    and (query_memory is not None or period_came_from_memory)
                )
            )
            and not has_explicit_intent
            and not has_explicit_filter
            and not has_payload_filter_value_in_message
        )
        if followup_short_detected:
            if confidence == "high":
                confidence = "medium"
            reasons.append("followup_short")

        if is_releves_query_tool and has_ambiguous_time_reference:
            confidence = "low"
            reasons.append("ambiguous_time_reference")

        if is_releves_query_tool and has_plan_date_range and not has_explicit_period:
            if confidence != "low":
                confidence = "medium"
            reasons.append("period_missing_in_message")
            if period_injected_from_memory:
                confidence = "low"
                reasons.append("period_injected_from_memory")

        if (
            followup_short_detected
            and query_memory is None
            and has_plan_date_range
            and not has_explicit_period
        ):
            confidence = "low"

        has_inferred_filter_conflict = (
            (bool(category_value) and not has_category_value_in_message)
            or (bool(merchant_value) and not has_merchant_value_in_message)
        )
        has_explicit_payload_conflict = merchant_conflict or category_conflict
        if merchant_conflict:
            confidence = "low"
            reasons.append("merchant_conflict")
        if category_conflict:
            confidence = "low"
            reasons.append("category_conflict")

        if (
            followup_short_detected
            and has_inferred_filter_conflict
            and not has_explicit_filter
            and (
                query_memory is None
                or has_ambiguous_time_reference
                or has_explicit_payload_conflict
            )
        ):
            confidence = "low"

        if plan.tool_name in {"finance_releves_sum", "finance_releves_search"}:
            has_categorie = isinstance(payload.get("categorie"), str) and bool(str(payload.get("categorie")).strip())
            if has_categorie and not any(token in normalized_message for token in ("catégorie", "categorie")) and not has_category_value_in_message:
                if confidence != "low":
                    confidence = "medium"
                reasons.append("category_inferred")

            has_merchant = isinstance(payload.get("merchant"), str) and bool(str(payload.get("merchant")).strip())
            if has_merchant and "chez" not in normalized_message and "merchant" not in normalized_message and "marchand" not in normalized_message and not has_merchant_value_in_message:
                if confidence != "low":
                    confidence = "medium"
                reasons.append("merchant_inferred")

        if has_explicit_intent:
            reasons.append("explicit_intent")
        if has_explicit_period:
            reasons.append("explicit_period")
        if has_explicit_filter:
            reasons.append("explicit_filter")

        has_obvious_tool = is_releves_query_tool
        if is_releves_query_tool and confidence == "high" and not ((has_explicit_intent or has_obvious_tool) and has_explicit_period):
            confidence = "medium"

        updated_meta = dict(plan.meta)
        updated_meta["confidence"] = confidence
        updated_meta["confidence_reasons"] = list(dict.fromkeys(reasons))
        return ToolCallPlan(
            tool_name=plan.tool_name,
            payload=dict(plan.payload),
            user_reply=plan.user_reply,
            meta=updated_meta,
        )

    @staticmethod
    def _canonicalize_plan_payload(
        plan: ToolCallPlan,
        known_categories: list[str] | None,
    ) -> None:
        if not known_categories:
            return
        if plan.tool_name not in {
            "finance_releves_sum",
            "finance_releves_search",
        }:
            return
        _canonicalize_category(plan.payload, known_categories)

    @staticmethod
    def _drop_none_payload_values(payload: dict[str, object]) -> dict[str, object]:
        """Remove null-equivalent fields before tool execution."""

        return {key: value for key, value in payload.items() if value is not None}

    def _guard_plan_with_llm_judge(
        self,
        message: str,
        plan: ToolCallPlan,
        *,
        query_memory: QueryMemory | None,
        known_categories: list[str] | None,
    ) -> ToolCallPlan | ClarificationPlan:
        confidence = plan.meta.get("confidence")
        if confidence == "high":
            return plan

        if self.llm_judge is None or not config.llm_enabled():
            if confidence == "low":
                return ClarificationPlan(
                    question=(
                        "Je veux éviter une erreur: pouvez-vous confirmer la période ou le filtre attendu ?"
                    ),
                    meta={"clarification_type": "low_confidence_plan"},
                )
            return plan

        context = query_memory.to_dict() if query_memory is not None else {}
        verdict = self.llm_judge.judge(
            user_message=message,
            deterministic_plan={
                "tool_name": plan.tool_name,
                "payload": dict(plan.payload),
            },
            conversation_context=context,
            known_categories=known_categories,
        )

        if verdict.meta.get("reason") == "judge_client_unavailable":
            if confidence == "low":
                return ClarificationPlan(
                    question=(
                        "Je veux éviter une erreur: pouvez-vous confirmer la période ou le filtre attendu ?"
                    ),
                    meta={"clarification_type": "low_confidence_plan"},
                )
            return plan

        if verdict.verdict == "clarify":
            question = verdict.question or "Pouvez-vous préciser votre demande ?"
            return ClarificationPlan(
                question=question,
                meta={"clarification_type": "llm_guardian", "llm_guardian": verdict.meta},
            )

        if verdict.verdict == "repair":
            tool_name = verdict.tool_name
            payload = verdict.payload
            if not isinstance(tool_name, str) or not isinstance(payload, dict):
                return plan
            allowed_tools = config.llm_allowed_tools()
            if tool_name not in allowed_tools:
                return ClarificationPlan(
                    question="Pouvez-vous préciser votre demande pour éviter une action incorrecte ?",
                    meta={"clarification_type": "llm_guardian_blocked_tool"},
                )
            is_valid, _, normalized_payload = self._validate_llm_tool_payload(
                tool_name,
                payload,
            )
            if not is_valid:
                return plan
            repaired_meta = dict(plan.meta)
            repaired_meta["llm_guardian"] = verdict.meta
            repaired_meta["llm_guardian_verdict"] = "repair"
            return ToolCallPlan(
                tool_name=tool_name,
                payload=normalized_payload,
                user_reply=verdict.user_reply or plan.user_reply,
                meta=repaired_meta,
            )

        approved_meta = dict(plan.meta)
        approved_meta["llm_guardian"] = verdict.meta
        approved_meta["llm_guardian_verdict"] = "approve"
        return ToolCallPlan(
            tool_name=plan.tool_name,
            payload=dict(plan.payload),
            user_reply=plan.user_reply,
            meta=approved_meta,
        )

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
    def plan_from_active_task(
        message: str,
        active_task: dict[str, object],
        *,
        known_categories: list[str] | None = None,
        query_memory: QueryMemory | None = None,
    ):
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

        if active_task_type == "clarification_pending":
            context = active_task.get("context")
            if not isinstance(context, dict):
                return ClarificationPlan(
                    question="Je n'ai pas compris la tâche en attente.",
                    meta={"clear_active_task": True},
                )

            clarification_type = context.get("clarification_type")
            if clarification_type == "prevent_write_on_followup":
                choice = _write_prevention_choice_from_message(message)
                focus_raw = context.get("focus")
                focus = (
                    _normalize_for_match(focus_raw)
                    if isinstance(focus_raw, str) and focus_raw.strip()
                    else ""
                )

                if choice is None:
                    return ClarificationPlan(
                        question="Tu veux parler d’un marchand ou d’une catégorie ?",
                        meta={
                            "keep_active_task": True,
                            "clarification_type": "prevent_write_on_followup",
                        },
                    )

                if not focus:
                    return ClarificationPlan(
                        question="Je n’ai pas trouvé le focus à analyser.",
                        meta={"clear_active_task": True},
                    )

                direction = "DEBIT_ONLY"
                base_last_query = context.get("base_last_query")
                if isinstance(base_last_query, dict):
                    filters = base_last_query.get("filters")
                    if isinstance(filters, dict):
                        raw_direction = filters.get("direction")
                        if raw_direction in {"DEBIT_ONLY", "CREDIT_ONLY", "ALL"}:
                            direction = raw_direction
                if (
                    isinstance(query_memory, QueryMemory)
                    and isinstance(query_memory.filters, dict)
                    and query_memory.filters.get("direction")
                    in {"DEBIT_ONLY", "CREDIT_ONLY", "ALL"}
                ):
                    direction = str(query_memory.filters["direction"])

                date_range = _date_range_from_pending_context(context, query_memory)

                if choice == "merchant":
                    payload: dict[str, object] = {
                        "merchant": focus,
                        "limit": 50,
                        "offset": 0,
                        "direction": direction,
                    }
                    if isinstance(date_range, dict):
                        payload["date_range"] = date_range
                    return ToolCallPlan(
                        tool_name="finance_releves_search",
                        payload=payload,
                        user_reply="OK.",
                        meta={"clear_active_task": True},
                    )

                matched_category = None
                for category_name in known_categories or []:
                    if _normalize_for_match(category_name) == focus:
                        matched_category = category_name
                        break
                if matched_category is None:
                    categories_display = ", ".join(known_categories or [])
                    question = f"Je ne trouve pas la catégorie « {focus_raw} »."
                    if categories_display:
                        question = (
                            f"{question} Voici vos catégories disponibles : {categories_display}."
                        )
                    return ClarificationPlan(
                        question=question,
                        meta={"clear_active_task": True},
                    )

                payload = {
                    "direction": direction,
                    "categorie": matched_category,
                }
                if isinstance(date_range, dict):
                    payload["date_range"] = date_range
                return ToolCallPlan(
                    tool_name="finance_releves_sum",
                    payload=payload,
                    user_reply="OK.",
                    meta={"clear_active_task": True},
                )

            if clarification_type == "merchant_vs_keyword":
                merchant_raw = context.get("merchant")
                keyword_raw = context.get("keyword")
                if not isinstance(merchant_raw, str) or not merchant_raw.strip():
                    clarification_payload = context.get("clarification_payload")
                    if isinstance(clarification_payload, dict):
                        merchant_from_payload = clarification_payload.get("merchant")
                        if (
                            isinstance(merchant_from_payload, str)
                            and merchant_from_payload.strip()
                        ):
                            merchant_raw = merchant_from_payload
                if not isinstance(keyword_raw, str) or not keyword_raw.strip():
                    clarification_payload = context.get("clarification_payload")
                    if isinstance(clarification_payload, dict):
                        keyword_from_payload = clarification_payload.get("keyword")
                        if (
                            isinstance(keyword_from_payload, str)
                            and keyword_from_payload.strip()
                        ):
                            keyword_raw = keyword_from_payload

                if (
                    not isinstance(merchant_raw, str)
                    or not merchant_raw.strip()
                    or not isinstance(keyword_raw, str)
                    or not keyword_raw.strip()
                ):
                    return ClarificationPlan(
                        question="Je n’ai pas compris les options proposées.",
                        meta={"clear_active_task": True},
                    )

                choice = _merchant_vs_keyword_choice_from_message(
                    message,
                    merchant=merchant_raw,
                    keyword=keyword_raw,
                )
                if choice is None:
                    return ClarificationPlan(
                        question=(
                            f"Tu veux le marchand ‘{merchant_raw}’ "
                            f"ou le mot-clé ‘{keyword_raw}’ ?"
                        ),
                        meta={
                            "keep_active_task": True,
                            "clarification_type": "merchant_vs_keyword",
                        },
                    )

                direction = "DEBIT_ONLY"
                if (
                    isinstance(query_memory, QueryMemory)
                    and isinstance(query_memory.filters, dict)
                    and query_memory.filters.get("direction")
                    in {"DEBIT_ONLY", "CREDIT_ONLY", "ALL"}
                ):
                    direction = str(query_memory.filters["direction"])

                payload: dict[str, object] = {
                    "limit": 50,
                    "offset": 0,
                    "direction": direction,
                }
                date_range = _date_range_from_pending_context(context, query_memory)
                if isinstance(date_range, dict):
                    payload["date_range"] = date_range

                if choice == "merchant":
                    payload["merchant"] = _normalize_for_match(merchant_raw)
                    payload.pop("search", None)
                else:
                    normalized_keyword = _normalize_for_match(keyword_raw)
                    if "search" in RelevesFilters.model_fields:
                        payload["search"] = normalized_keyword
                        payload.pop("merchant", None)
                    else:
                        payload["merchant"] = normalized_keyword
                        payload.pop("search", None)

                return ToolCallPlan(
                    tool_name="finance_releves_search",
                    payload=payload,
                    user_reply="OK.",
                    meta={"clear_active_task": True},
                )

            period_payload = context.get("period_payload")
            if not isinstance(period_payload, dict) or not period_payload:
                return ClarificationPlan(
                    question="Je n'ai pas compris la période demandée.",
                    meta={"clear_active_task": True},
                )

            if clarification_type in {"direction_choice", "missing_direction"}:
                direction = AgentLoop._direction_from_clarification_message(message)
                if direction is None:
                    return ClarificationPlan(
                        question="Tu veux les dépenses, revenus ou les deux ?",
                        meta={"keep_active_task": True},
                    )

                base_payload = context.get("base_payload")
                payload: dict[str, object] = {}
                if isinstance(base_payload, dict):
                    payload.update(base_payload)
                payload.update(period_payload)
                payload["direction"] = direction
                return ToolCallPlan(
                    tool_name="finance_releves_sum",
                    payload=payload,
                    user_reply="OK.",
                    meta={"clear_active_task": True},
                )

            category = AgentLoop._category_from_clarification_message(
                message,
                known_categories or [],
            )
            if not category:
                return ClarificationPlan(
                    question="Pour quelle catégorie veux-tu ce calcul ?",
                    meta={"keep_active_task": True},
                )

            base_last_query = context.get("base_last_query")
            direction = "DEBIT_ONLY"
            if isinstance(base_last_query, dict):
                filters = base_last_query.get("filters")
                if isinstance(filters, dict):
                    raw_direction = filters.get("direction")
                    if raw_direction in {"DEBIT_ONLY", "CREDIT_ONLY", "ALL"}:
                        direction = raw_direction

            payload: dict[str, object] = {
                "direction": direction,
                **period_payload,
                "categorie": category,
            }
            return ToolCallPlan(
                tool_name="finance_releves_sum",
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
    def _category_from_clarification_message(
        message: str,
        known_categories: list[str],
    ) -> str | None:
        cleaned = message.strip().strip(" .,!?:;\"'“”«»")
        if not cleaned:
            return None

        normalized_message = _normalize_for_match(cleaned)
        for category_name in known_categories:
            normalized_category = _normalize_for_match(category_name)
            if normalized_category and normalized_category in normalized_message:
                return category_name

        match = re.search(
            r"(?:pour\s+)?(?:la\s+)?cat[eé]gorie\s+(.+)$",
            cleaned,
            flags=re.IGNORECASE,
        )
        candidate = match.group(1).strip() if match is not None else cleaned
        candidate = candidate.strip(" .,!?:;\"'“”«»")
        if not candidate:
            return None

        for category_name in known_categories:
            if _normalize_for_match(category_name) == _normalize_for_match(candidate):
                return category_name

        return candidate

    @staticmethod
    def _direction_from_clarification_message(message: str) -> str | None:
        normalized = _normalize_for_match(message)
        if not normalized:
            return None

        if normalized in _DIRECTION_BOTH_WORDS:
            return "ALL"
        if normalized in _DIRECTION_DEBIT_WORDS:
            return "DEBIT_ONLY"
        if normalized in _DIRECTION_CREDIT_WORDS:
            return "CREDIT_ONLY"
        return None

    @staticmethod
    def _is_active_task_stale(active_task: dict[str, object]) -> bool:
        created_at_raw = active_task.get("created_at")
        if not isinstance(created_at_raw, str) or not created_at_raw.strip():
            return True

        created_at_value = created_at_raw.strip()
        if created_at_value.endswith("Z"):
            created_at_value = f"{created_at_value[:-1]}+00:00"

        try:
            created_at = datetime.fromisoformat(created_at_value)
        except ValueError:
            return True

        if created_at.tzinfo is None:
            return True

        age_seconds = (datetime.now(timezone.utc) - created_at).total_seconds()
        return age_seconds > _ACTIVE_TASK_TTL_SECONDS

    @staticmethod
    def _should_ignore_clarification_pending(
        message: str,
        active_task: dict[str, object],
        *,
        known_categories: list[str] | None = None,
    ) -> bool:
        del active_task

        stripped_message = message.strip()
        if not stripped_message:
            return False

        if AgentLoop._direction_from_clarification_message(stripped_message) is not None:
            return False

        if _normalize_for_match(stripped_message) in _CONFIRM_WORDS | _REJECT_WORDS:
            return False

        if AgentLoop._category_from_clarification_message(
            stripped_message,
            known_categories or [],
        ) is not None and len(_normalize_for_match(stripped_message).split()) <= 3:
            return False

        normalized_message = _normalize_for_match(stripped_message)
        tokens = normalized_message.split()

        if _CONFIDENCE_EXPLICIT_DATE_PATTERN.search(stripped_message):
            return True
        if _CONFIDENCE_YEAR_PATTERN.search(normalized_message):
            return True
        if any(month in normalized_message for month in _CONFIDENCE_MONTH_TOKENS):
            return True
        if any(intent_word in tokens for intent_word in _NEW_REQUEST_INTENT_WORDS):
            return True
        return False

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
            if confirmation_type == "confirm_delete_category_suggestion":
                return NoopPlan(
                    reply="D’accord, suppression annulée.",
                    meta={"clear_active_task": True},
                )
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
            meta: dict[str, object] = {"clear_active_task": True}
            if context.get("category_prechecked") is True:
                meta["category_prechecked"] = True
            return ToolCallPlan(
                tool_name="finance_categories_delete",
                payload={"category_name": target_name},
                user_reply="Catégorie supprimée.",
                meta=meta,
            )

        if confirmation_type == "confirm_delete_category_suggestion":
            suggested_name = str(context.get("suggested_name", "")).strip()
            if not suggested_name:
                return NoopPlan(
                    reply="D’accord, suppression annulée.",
                    meta={"clear_active_task": True},
                )
            return SetActiveTaskPlan(
                reply=(
                    f"Confirmez-vous la suppression de la catégorie « {suggested_name} » ? "
                    "Répondez OUI ou NON."
                ),
                active_task={
                    "type": "needs_confirmation",
                    "confirmation_type": "confirm_delete_category",
                    "context": {
                        "category_name": suggested_name,
                        "category_prechecked": True,
                    },
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
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
            write_tools = _RISKY_WRITE_TOOLS | _SOFT_WRITE_TOOLS
            if (
                not isinstance(tool_name, str)
                or not tool_name.strip()
                or tool_name not in write_tools
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
                plan=AgentLoop._serialize_plan(list_plan),
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
    def _precheck_categories_delete_by_name(
        plan: ToolCallPlan,
        *,
        tool_router: ToolRouter,
        profile_id: UUID,
    ) -> AgentReply | None:
        if plan.tool_name != "finance_categories_delete":
            return None

        category_name = plan.payload.get("category_name") if isinstance(plan.payload, dict) else None
        if not isinstance(category_name, str) or not category_name.strip():
            return None

        requested_name = category_name.strip()
        list_plan = ToolCallPlan(tool_name="finance_categories_list", payload={}, user_reply="")
        list_result = tool_router.call("finance_categories_list", {}, profile_id=profile_id)
        normalized_list_result = AgentLoop._normalize_tool_result("finance_categories_list", list_result)
        if isinstance(normalized_list_result, ToolError):
            return AgentReply(
                reply=build_final_reply(plan=list_plan, tool_result=normalized_list_result),
                tool_result=AgentLoop._serialize_tool_result(normalized_list_result),
                plan=AgentLoop._serialize_plan(list_plan),
            )

        items = getattr(normalized_list_result, "items", None)
        if not isinstance(items, list):
            return None

        requested_norm = _normalize_for_match(requested_name)
        category_names = [
            item.name.strip()
            for item in items
            if isinstance(getattr(item, "name", None), str) and item.name.strip()
        ]
        if any(_normalize_for_match(name) == requested_norm for name in category_names):
            return None

        names_by_norm: dict[str, str] = {}
        for name in category_names:
            name_norm = _normalize_for_match(name)
            if name_norm and name_norm not in names_by_norm:
                names_by_norm[name_norm] = name

        suggestions: list[str] = []
        for name_norm, display_name in names_by_norm.items():
            if requested_norm in name_norm or name_norm in requested_norm:
                suggestions.append(display_name)

        if len(suggestions) < 3:
            close_norms = get_close_matches(
                requested_norm,
                list(names_by_norm.keys()),
                n=3,
                cutoff=0.6,
            )
            for close_norm in close_norms:
                display_name = names_by_norm[close_norm]
                if display_name not in suggestions:
                    suggestions.append(display_name)

        error = ToolError(
            code=ToolErrorCode.NOT_FOUND,
            message="Category not found for provided name.",
            details={
                "category_name": requested_name,
                "close_category_names": suggestions,
                "available_category_names": category_names,
            },
        )

        reply = f"Je ne trouve pas la catégorie « {requested_name} »."
        if suggestions:
            suggested_name = suggestions[0]
            return AgentReply(
                reply=(
                    f"Je ne trouve pas « {requested_name} ». "
                    f"Voulez-vous dire « {suggested_name} » ? (oui/non)"
                ),
                tool_result=AgentLoop._serialize_tool_result(error),
                plan=AgentLoop._serialize_plan(plan),
                active_task={
                    "type": "needs_confirmation",
                    "confirmation_type": "confirm_delete_category_suggestion",
                    "context": {
                        "requested_name": requested_name,
                        "suggested_name": suggested_name,
                        "suggestions": suggestions,
                    },
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                should_update_active_task=True,
            )
        elif category_names:
            reply = f"{reply} Voici vos catégories disponibles : {', '.join(category_names)}."

        return AgentReply(
            reply=reply,
            tool_result=AgentLoop._serialize_tool_result(error),
            plan=AgentLoop._serialize_plan(plan),
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
    def _serialize_plan(plan: ToolCallPlan) -> dict[str, object]:
        serialized_plan: dict[str, object] = {
            "tool_name": plan.tool_name,
            "payload": plan.payload,
        }
        if isinstance(plan.meta, dict) and plan.meta:
            visible_meta = {
                key: value
                for key, value in plan.meta.items()
                if key.startswith("debug_")
            }
            if visible_meta:
                serialized_plan["meta"] = visible_meta
        return serialized_plan

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
        memory: dict[str, object] | None = None,
        debug: bool = False,
    ) -> AgentReply:
        query_memory = (
            QueryMemory.from_dict(memory.get("last_query"))
            if isinstance(memory, dict)
            else None
        )
        logger.info(
            "handle_user_message_started active_task_present=%s query_memory_present=%s query_last_tool_name=%s",
            isinstance(active_task, dict),
            query_memory is not None,
            query_memory.last_tool_name if query_memory is not None else None,
        )
        known_categories = self._known_categories_from_memory(memory)
        active_task_effective = active_task
        should_force_clear_active_task = False
        if (
            isinstance(active_task, dict)
            and active_task.get("type") == "clarification_pending"
            and (
                self._is_active_task_stale(active_task)
                or self._should_ignore_clarification_pending(
                    message,
                    active_task,
                    known_categories=known_categories,
                )
            )
        ):
            active_task_effective = None
            should_force_clear_active_task = True

        followup_plan = None
        if active_task_effective is None and query_memory is not None:
            followup_plan = followup_plan_from_message(
                message,
                query_memory,
                known_categories=known_categories,
            )
            if isinstance(followup_plan, ToolCallPlan):
                logger.info(
                    "handle_user_message_followup_plan tool_name=%s payload=%s",
                    followup_plan.tool_name,
                    followup_plan.payload,
                )

        if followup_plan is not None:
            plan = followup_plan
        elif (
            isinstance(active_task_effective, dict)
            and active_task_effective.get("type") == "clarification_pending"
        ):
            plan = self.plan_from_active_task(
                message,
                active_task_effective,
                known_categories=known_categories,
                query_memory=query_memory,
            )
        else:
            routed = self._route_message(
                message,
                profile_id=profile_id,
                active_task=active_task_effective,
            )
            if isinstance(routed, AgentReply):
                if should_force_clear_active_task:
                    return AgentReply(
                        reply=routed.reply,
                        tool_result=routed.tool_result,
                        plan=routed.plan,
                        active_task=None,
                        should_update_active_task=True,
                        memory_update=routed.memory_update,
                    )
                return routed
            plan = routed

        self._run_llm_shadow(
            message,
            profile_id=profile_id,
            active_task=active_task_effective,
            deterministic_plan=plan,
        )

        if isinstance(plan, ToolCallPlan):
            plan = self._with_confidence_meta(
                message,
                plan,
                query_memory=query_memory,
            )
            plan = self._guard_plan_with_llm_judge(
                message,
                plan,
                query_memory=query_memory,
                known_categories=known_categories,
            )

        if (
            isinstance(plan, ToolCallPlan)
            and active_task_effective is None
            and plan.tool_name in _WRITE_TOOLS
            and is_followup_message(message)
        ):
            followup_focus = _extract_followup_focus_for_write_prevention(message)
            clarification_question = (
                f"Tu parles de « {followup_focus} » comme marchand ou comme catégorie ?"
                if followup_focus
                else "Tu parles d’un marchand ou d’une catégorie ?"
            )
            plan = ClarificationPlan(
                question=clarification_question,
                meta={
                    "keep_active_task": True,
                    "clarification_type": "prevent_write_on_followup",
                },
            )

        plan_meta = (
            getattr(plan, "meta", {})
            if isinstance(getattr(plan, "meta", {}), dict)
            else {}
        )
        should_update_active_task = False
        updated_active_task = active_task_effective
        if should_force_clear_active_task:
            should_update_active_task = True
            updated_active_task = None
        if plan_meta.get("clear_active_task"):
            should_update_active_task = True
            updated_active_task = None
        elif plan_meta.get("keep_active_task"):
            should_update_active_task = True
            updated_active_task = active_task_effective
            clarification_type = plan_meta.get("clarification_type")
            clarification_payload = plan_meta.get("clarification_payload")
            if active_task_effective is None and clarification_type == "awaiting_search_merchant":
                updated_active_task = {"type": "awaiting_search_merchant"}
                if isinstance(clarification_payload, dict):
                    date_range = clarification_payload.get("date_range")
                    if isinstance(date_range, dict):
                        updated_active_task["date_range"] = date_range

        if isinstance(plan, SetActiveTaskPlan):
            if profile_id is not None:
                active_task_context = (
                    plan.active_task if isinstance(plan.active_task, dict) else {}
                )
                if (
                    active_task_context.get("type") == "needs_confirmation"
                    and active_task_context.get("confirmation_type")
                    == "confirm_delete_category"
                ):
                    context = active_task_context.get("context")
                    category_name = (
                        context.get("category_name")
                        if isinstance(context, dict)
                        else None
                    )
                    category_prechecked = (
                        context.get("category_prechecked") is True
                        if isinstance(context, dict)
                        else False
                    )
                    if (
                        isinstance(category_name, str)
                        and category_name.strip()
                        and not category_prechecked
                    ):
                        precheck_reply = self._precheck_categories_delete_by_name(
                            ToolCallPlan(
                                tool_name="finance_categories_delete",
                                payload={"category_name": category_name.strip()},
                                user_reply="",
                            ),
                            tool_router=self.tool_router,
                            profile_id=profile_id,
                        )
                        if precheck_reply is not None:
                            return precheck_reply
                        if isinstance(context, dict):
                            context["category_prechecked"] = True

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

        if isinstance(plan, ToolCallPlan):
            if (
                plan.tool_name == "finance_categories_delete"
                and profile_id is not None
                and plan.meta.get("category_prechecked") is not True
            ):
                precheck_reply = self._precheck_categories_delete_by_name(
                    plan,
                    tool_router=self.tool_router,
                    profile_id=profile_id,
                )
                if precheck_reply is not None:
                    return precheck_reply

        if (
            isinstance(plan, ToolCallPlan)
            and plan.tool_name in _RISKY_WRITE_TOOLS
            and active_task_effective is None
        ):
            confirmation_plan = _wrap_write_plan_with_confirmation(plan)
            return AgentReply(
                reply=confirmation_plan.reply,
                active_task=confirmation_plan.active_task,
                should_update_active_task=True,
            )

        if isinstance(plan, ToolCallPlan):
            payload_before_memory = dict(plan.payload)
            plan, _memory_reason = apply_memory_to_plan(message, plan, query_memory)
            self._canonicalize_plan_payload(plan, known_categories)

            payload_after_memory = dict(plan.payload)
            injected_fields = {
                key: value
                for key, value in payload_after_memory.items()
                if key not in payload_before_memory or payload_before_memory[key] != value
            }

            if debug and (query_memory is not None or followup_plan is not None or injected_fields):
                plan.meta["debug_memory_injected"] = injected_fields
                plan.meta["debug_query_memory_used"] = query_memory.to_dict() if query_memory is not None else None
                plan.meta["debug_followup_used"] = followup_plan is not None

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
            plan.payload = self._drop_none_payload_values(plan.payload)
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
                        plan=self._serialize_plan(plan),
                        active_task=active_task_plan.active_task,
                        should_update_active_task=True,
                    )
            final_reply = build_final_reply(plan=plan, tool_result=result)
            logger.info("tool_execution_completed tool_name=%s", plan.tool_name)
            extracted_memory = (
                extract_memory_from_plan(
                    plan.tool_name,
                    plan.payload,
                    plan.meta,
                    known_categories=known_categories,
                )
                if not isinstance(result, ToolError)
                else None
            )
            categories_cache_update = self._categories_cache_update(
                tool_name=plan.tool_name,
                result=result,
            )
            merged_memory_update: dict[str, object] | None = None
            if extracted_memory is not None:
                merged_memory_update = {"last_query": extracted_memory.to_dict()}
            if categories_cache_update is not None:
                if merged_memory_update is None:
                    merged_memory_update = {}
                merged_memory_update.update(categories_cache_update)
            return AgentReply(
                reply=final_reply,
                tool_result=self._serialize_tool_result(result),
                plan=self._serialize_plan(plan),
                active_task=updated_active_task,
                should_update_active_task=should_update_active_task,
                memory_update=merged_memory_update,
            )

        if isinstance(plan, ClarificationPlan):
            clarification_type = plan_meta.get("clarification_type")
            clarification_payload = plan_meta.get("clarification_payload")
            pending_period_payload = period_payload_from_message(message)
            if (
                active_task_effective is None
                and plan_meta.get("keep_active_task")
                and clarification_type != "awaiting_search_merchant"
            ):
                pending_context: dict[str, object] = {}
                if pending_period_payload and clarification_type != "awaiting_search_merchant":
                    pending_context["period_payload"] = pending_period_payload

                if isinstance(clarification_type, str) and clarification_type:
                    normalized_clarification_type = clarification_type
                    if clarification_type in {
                        "missing_direction",
                        "direction",
                        "missing_direction_choice",
                    }:
                        normalized_clarification_type = "direction_choice"
                    pending_context["clarification_type"] = normalized_clarification_type

                pending_context["clarification_question"] = plan.question
                if isinstance(query_memory, QueryMemory):
                    pending_context["base_last_query"] = query_memory.to_dict()
                    if isinstance(query_memory.filters, dict):
                        pending_context["base_payload"] = dict(query_memory.filters)
                    if isinstance(query_memory.date_range, dict):
                        pending_context["period_payload"] = {
                            "date_range": dict(query_memory.date_range)
                        }

                if clarification_type == "prevent_write_on_followup":
                    focus = _extract_followup_focus_for_write_prevention(message)
                    if focus:
                        pending_context["focus"] = focus
                    if known_categories:
                        pending_context["known_categories"] = list(known_categories)

                if clarification_type == "merchant_vs_keyword":
                    if isinstance(clarification_payload, dict):
                        pending_context["clarification_payload"] = dict(
                            clarification_payload
                        )
                        merchant = clarification_payload.get("merchant")
                        keyword = clarification_payload.get("keyword")
                        if isinstance(merchant, str) and merchant.strip():
                            pending_context["merchant"] = merchant
                        if isinstance(keyword, str) and keyword.strip():
                            pending_context["keyword"] = keyword

                updated_active_task = {
                    "type": "clarification_pending",
                    "context": pending_context,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                should_update_active_task = True

            response_payload = (
                clarification_payload if isinstance(clarification_payload, dict) else None
            )
            if debug and isinstance(updated_active_task, dict):
                pending_context = updated_active_task.get("context")
                if isinstance(pending_context, dict):
                    pending_debug = pending_context.get("period_payload")
                    if isinstance(pending_debug, dict) and pending_debug:
                        if response_payload is None:
                            response_payload = {}
                        response_payload["debug_pending_clarification_context"] = {
                            "period_payload": pending_debug
                        }

            return AgentReply(
                reply=plan.question,
                tool_result=_build_clarification_tool_result(
                    message=plan.question,
                    clarification_type=(
                        clarification_type
                        if isinstance(clarification_type, str) and clarification_type
                        else "generic"
                    ),
                    payload=response_payload,
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

    @staticmethod
    def _known_categories_from_memory(
        memory: dict[str, object] | None,
    ) -> list[str]:
        if not isinstance(memory, dict):
            return []
        raw_categories = memory.get("known_categories")
        if not isinstance(raw_categories, list):
            return []
        return [
            category.strip()
            for category in raw_categories
            if isinstance(category, str) and category.strip()
        ]

    @staticmethod
    def _categories_cache_update(
        *,
        tool_name: str,
        result: object,
    ) -> dict[str, object] | None:
        if tool_name != "finance_categories_list" or isinstance(result, ToolError):
            return None

        items = getattr(result, "items", None)
        if not isinstance(items, list):
            return None

        names: list[str] = []
        for item in items:
            name = getattr(item, "name", None)
            if isinstance(name, str) and name.strip():
                names.append(name.strip())

        return {"known_categories": names}

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
