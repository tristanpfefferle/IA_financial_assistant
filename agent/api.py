"""FastAPI entrypoint for agent HTTP endpoints."""

from __future__ import annotations

import logging
import inspect
import os
import re
import unicodedata
import calendar
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import lru_cache
from typing import Any
from datetime import date, datetime
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, Response
from fastapi.requests import Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from shared import config as _config
from agent.backend_client import BackendClient
from agent.llm_planner import LLMPlanner
from agent.loop import AgentLoop
from agent.memory import period_payload_from_message
from agent.tool_router import ToolRouter
from agent.bank_catalog import extract_canonical_banks
from agent.merchant_cleanup import MerchantSuggestion, run_merchant_cleanup
from agent.merchant_alias_resolver import resolve_pending_map_alias
from agent.import_label_normalizer import extract_observed_alias_from_label
from backend.factory import build_backend_tool_service
from backend.reporting import (
    SpendingCategoryRow,
    SpendingReportData,
    SpendingTransactionRow,
    generate_spending_report_pdf,
)
from backend.auth.supabase_auth import UnauthorizedError, get_user_from_bearer_token
from backend.db.supabase_client import SupabaseClient, SupabaseSettings
from backend.repositories.profiles_repository import ProfilesRepository, SupabaseProfilesRepository
from shared.models import RelevesDirection, ToolError, ToolErrorCode


logger = logging.getLogger(__name__)


_GLOBAL_STATE_MODES = {"onboarding", "guided_budget", "free_chat"}
_GLOBAL_STATE_ONBOARDING_STEPS = {"profile", "bank_accounts", "import", "categories", "budget", "report", None}
_GLOBAL_STATE_ONBOARDING_SUBSTEPS = {
    "profile_collect",
    "profile_confirm",
    "bank_accounts_collect",
    "bank_accounts_confirm",
    "import_select_account",
    "categories_intro",
    "categories_bootstrap",
    "categories_review",
    "report_offer",
    "report_sent",
    None,
}
_PROFILE_COMPLETION_FIELDS = ("first_name", "last_name", "birth_date")
_ONBOARDING_NAME_PATTERN = re.compile(
    r"^\s*([A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'\-]+)\s+([A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'\-]+)\s*$"
)
_ONBOARDING_NAME_PREFIX_PATTERN = re.compile(
    r"^\s*([A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'\-]+)\s+([A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'\-]+)\b"
)
_ONBOARDING_BIRTH_DATE_PATTERN = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_ONBOARDING_BIRTH_DATE_DOT_PATTERN = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
_ONBOARDING_BIRTH_DATE_SLASH_PATTERN = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_ONBOARDING_BIRTH_DATE_MONTH_NAME_PATTERN = re.compile(r"^(\d{1,2})\s+([a-z]+)\s+(\d{4})$")
_ONBOARDING_BIRTH_DATE_IN_TEXT_PATTERNS = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}\.\d{1,2}\.\d{4}\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b"),
    re.compile(r"\b\d{1,2}\s+[A-Za-zÀ-ÖØ-öø-ÿ]+\s+\d{4}\b", flags=re.IGNORECASE),
)
_FRENCH_MONTH_TO_NUMBER = {
    "janvier": 1,
    "janv": 1,
    "fevrier": 2,
    "fevr": 2,
    "fev": 2,
    "mars": 3,
    "avril": 4,
    "avr": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "juil": 7,
    "aout": 8,
    "septembre": 9,
    "sept": 9,
    "octobre": 10,
    "oct": 10,
    "novembre": 11,
    "nov": 11,
    "decembre": 12,
    "dec": 12,
}
_BANK_ACCOUNTS_REQUEST_HINTS = ("liste", "catégor", "depens", "dépens", "recett", "transaction", "relev")
_YES_VALUES = {"oui", "ouais", "yep", "yes", "y", "ok", "daccord", "confirm", "je confirme"}
_NO_VALUES = {"non", "nope", "no", "n"}
_IMPORT_FILE_PROMPT = "Parfait. Envoie le fichier CSV/PDF du compte sélectionné."
_SYSTEM_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("food", "Alimentation"),
    ("housing", "Logement"),
    ("transport", "Transport"),
    ("health", "Santé"),
    ("leisure", "Loisirs"),
    ("shopping", "Shopping"),
    ("bills", "Factures"),
    ("taxes", "Impôts"),
    ("insurance", "Assurance"),
    ("other", "Autres"),
)
_MERCHANT_CATEGORY_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("migros", "coop", "lidl", "aldi", "denner"), "Alimentation"),
    (("sbb", "cff", "tpg", "tl", "uber", "bolt"), "Transport"),
    (("swisscom", "salt", "sunrise"), "Factures"),
    (("axa", "zurich", "helvetia", "mobiliar"), "Assurance"),
)
_FALLBACK_MERCHANT_CATEGORY = "Autres"


def _build_system_categories_payload() -> list[dict[str, str]]:
    """Return canonical default system categories payload for repository bootstrap."""

    return [
        {"system_key": system_key, "name": category_name}
        for system_key, category_name in _SYSTEM_CATEGORIES
    ]


def _classify_merchants_without_category(*, profiles_repository: Any, profile_id: UUID) -> tuple[int, int, int]:
    """Classify merchants without category and return classified, remaining valid, and invalid counts."""

    merchants_without_category = profiles_repository.list_merchants_without_category(profile_id=profile_id)
    classified_count = 0
    for merchant in merchants_without_category:
        merchant_id = merchant.get("id")
        if merchant_id is None:
            continue
        try:
            merchant_uuid = merchant_id if isinstance(merchant_id, UUID) else UUID(str(merchant_id))
        except ValueError:
            continue
        merchant_name = str(merchant.get("name_norm") or merchant.get("name") or "")
        category_name = _pick_category_for_merchant_name(merchant_name)
        try:
            profiles_repository.update_merchant_category(
                merchant_id=merchant_uuid,
                category_name=category_name,
            )
        except Exception:
            logger.exception(
                "categories_update_merchant_failed merchant_id=%s",
                merchant_uuid,
            )
            continue
        classified_count += 1

    merchants_without_category_after = profiles_repository.list_merchants_without_category(profile_id=profile_id)
    remaining_count = 0
    invalid_count = 0
    for merchant in merchants_without_category_after:
        merchant_id = merchant.get("id")
        if merchant_id is None:
            invalid_count += 1
            continue
        try:
            _ = merchant_id if isinstance(merchant_id, UUID) else UUID(str(merchant_id))
        except (TypeError, ValueError):
            invalid_count += 1
            continue
        remaining_count += 1

    return classified_count, remaining_count, invalid_count


def _build_import_file_ui_request(import_context: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a UI upload request payload when import context includes a selected account."""

    if not isinstance(import_context, dict):
        return None

    bank_account_id = import_context.get("selected_bank_account_id")
    if not isinstance(bank_account_id, str) or not bank_account_id.strip():
        return None

    bank_account_name = import_context.get("selected_bank_account_name")
    return {
        "type": "ui_request",
        "name": "import_file",
        "bank_account_id": bank_account_id,
        "bank_account_name": str(bank_account_name or ""),
        "accepted_types": ["csv", "pdf"],
    }


def _build_open_pdf_ui_request(url: str) -> dict[str, str]:
    """Return an UI request payload instructing the client to open a PDF URL."""

    return {
        "type": "ui_request",
        "name": "open_pdf_report",
        "url": url,
    }


def _build_spending_pdf_url(*, month: str | None = None, start_date: str | None = None, end_date: str | None = None) -> str:
    """Build relative spending report endpoint URL with optional period filters."""

    if isinstance(month, str) and month.strip():
        return f"/finance/reports/spending.pdf?month={month.strip()}"

    if isinstance(start_date, str) and isinstance(end_date, str) and start_date.strip() and end_date.strip():
        return f"/finance/reports/spending.pdf?start_date={start_date.strip()}&end_date={end_date.strip()}"

    return "/finance/reports/spending.pdf"


def _extract_import_date_range(result: dict[str, Any]) -> dict[str, str] | None:
    """Infer import date range from preview rows when available."""

    preview_items = result.get("preview")
    if not isinstance(preview_items, list):
        return None

    valid_dates: list[date] = []
    for item in preview_items:
        if not isinstance(item, dict):
            continue
        raw_date = item.get("date")
        if not isinstance(raw_date, str):
            continue
        try:
            valid_dates.append(date.fromisoformat(raw_date))
        except ValueError:
            continue

    if not valid_dates:
        return None

    return {
        "start": min(valid_dates).isoformat(),
        "end": max(valid_dates).isoformat(),
    }


def _build_pending_clarification_from_tool_result(
    tool_result: Any,
) -> dict[str, Any] | None:
    """Build persisted pending clarification context from a clarification tool result."""

    if not isinstance(tool_result, dict) or tool_result.get("type") != "clarification":
        return None

    payload = tool_result.get("payload")
    clarification_type = tool_result.get("clarification_type")
    message = tool_result.get("message")

    if not isinstance(payload, dict):
        if isinstance(clarification_type, str) and isinstance(message, str) and message.strip():
            missing_fields = (
                ["date_range"]
                if _clarification_type_indicates_missing_period(clarification_type)
                else []
            )
            return {
                "type": "clarification_pending",
                "tool_name": None,
                "intent": None,
                "partial_payload": {},
                "missing_fields": missing_fields,
                "mode": "date_range_only",
                "clarification_type": clarification_type,
            }
        return None

    tool_name = payload.get("tool_name") or payload.get("expected_tool_name")
    if not isinstance(tool_name, str) or not tool_name.strip():
        return None

    partial_payload = payload.get("partial_payload")
    if not isinstance(partial_payload, dict):
        partial_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}

    missing_fields_raw = payload.get("missing_fields")
    missing_fields = [
        str(field).strip()
        for field in missing_fields_raw
        if isinstance(field, str) and str(field).strip()
    ] if isinstance(missing_fields_raw, list) else []

    return {
        "type": "clarification_pending",
        "tool_name": tool_name.strip(),
        "intent": payload.get("intent") if isinstance(payload.get("intent"), str) else None,
        "partial_payload": dict(partial_payload),
        "missing_fields": missing_fields,
    }


def _clarification_type_indicates_missing_period(clarification_type: str) -> bool:
    """Return True when clarification type semantically requests a missing period/date range."""

    normalized = clarification_type.strip().casefold()
    return any(
        token in normalized
        for token in ("missing_date_range", "date_range", "period", "periode", "période")
    )


def _resolve_pending_clarification_payload(
    *,
    message: str,
    pending_clarification: dict[str, Any],
    state_dict: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any], list[str]] | None:
    """Return tool name/payload/remaining missing fields for a pending clarification."""

    tool_name = pending_clarification.get("tool_name")
    mode = pending_clarification.get("mode")

    if (not isinstance(tool_name, str) or not tool_name.strip()) and mode == "date_range_only":
        last_query = state_dict.get("last_query") if isinstance(state_dict, dict) else None
        last_tool_name = last_query.get("last_tool_name") if isinstance(last_query, dict) else None
        inferred_tool_name = (
            last_tool_name.strip()
            if isinstance(last_tool_name, str) and last_tool_name.strip()
            else "finance_releves_sum"
        )
        tool_name = inferred_tool_name

        last_filters = last_query.get("filters") if isinstance(last_query, dict) else None
        base_payload: dict[str, Any] = {}
        if isinstance(last_filters, dict):
            base_payload.update(last_filters)

        payload = {**base_payload, **dict(pending_clarification.get("partial_payload") or {})}
        if inferred_tool_name == "finance_releves_sum":
            payload["categorie"] = None

        period_payload = period_payload_from_message(message)
        if not isinstance(period_payload, dict):
            return None
        date_range = period_payload.get("date_range")
        if not isinstance(date_range, dict):
            return None
        payload["date_range"] = date_range

        return inferred_tool_name, payload, []

    if not isinstance(tool_name, str) or not tool_name.strip():
        return None

    payload = dict(pending_clarification.get("partial_payload") or {})
    missing_fields_raw = pending_clarification.get("missing_fields")
    missing_fields = [
        str(field).strip()
        for field in missing_fields_raw
        if isinstance(field, str) and str(field).strip()
    ] if isinstance(missing_fields_raw, list) else []

    if "date_range" in missing_fields and "date_range" not in payload:
        period_payload = period_payload_from_message(message)
        if isinstance(period_payload, dict):
            date_range = period_payload.get("date_range")
            if isinstance(date_range, dict):
                payload["date_range"] = date_range

    remaining_missing_fields = [field for field in missing_fields if payload.get(field) is None]
    return tool_name.strip(), payload, remaining_missing_fields


def _build_pending_resolution_reply(
    *,
    tool_name: str,
    tool_payload: dict[str, Any],
    pending_result: Any,
) -> tuple[str, Any]:
    """Build user-facing reply for a pending clarification execution result."""

    build_reply = None
    tool_call_plan_type = None
    try:
        from agent.answer_builder import build_final_reply as _build_final_reply
        from agent.planner import ToolCallPlan as _ToolCallPlan

        build_reply = _build_final_reply
        tool_call_plan_type = _ToolCallPlan
    except Exception:
        logger.exception("pending_clarification_reply_import_failed tool=%s", tool_name)

    if build_reply is not None and tool_call_plan_type is not None and isinstance(pending_result, ToolError):
        plan = tool_call_plan_type(tool_name=tool_name, payload=dict(tool_payload), user_reply="")
        return build_reply(plan=plan, tool_result=pending_result), jsonable_encoder(pending_result)

    if build_reply is not None and tool_call_plan_type is not None:
        try:
            plan = tool_call_plan_type(tool_name=tool_name, payload=dict(tool_payload), user_reply="")
            reply = build_reply(plan=plan, tool_result=pending_result)
            if "résultat indisponible" not in reply:
                return reply, jsonable_encoder(pending_result)
        except Exception:
            logger.exception("pending_clarification_reply_build_failed tool=%s", tool_name)

    if isinstance(pending_result, dict):
        total = pending_result.get("total")
        count = pending_result.get("count")
        currency = pending_result.get("currency")
        if isinstance(count, int) and isinstance(currency, str):
            try:
                decimal_total = Decimal(str(total))
            except (InvalidOperation, TypeError, ValueError):
                decimal_total = None
            if decimal_total is not None:
                quantized_total = decimal_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                average = (
                    (decimal_total / Decimal(count)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    if count > 0
                    else Decimal("0.00")
                )
                return (
                    f"Total des dépenses: {quantized_total:.2f} {currency} sur {count} opération(s). "
                    f"Moyenne: {average:.2f} {currency}.",
                    jsonable_encoder(pending_result),
                )
        if "groups" in pending_result and isinstance(pending_result.get("groups"), dict):
            groups = pending_result["groups"]
            if not groups:
                return "Je n'ai trouvé aucune opération pour cette agrégation.", jsonable_encoder(pending_result)
            lines = ["Voici vos dépenses agrégées :"]
            for name, values in list(groups.items())[:10]:
                if isinstance(values, dict):
                    group_total = values.get("total", 0)
                    group_count = values.get("count", 0)
                    lines.append(f"- {name}: {group_total} ({group_count} opérations)")
            return "\n".join(lines), jsonable_encoder(pending_result)

    return "C'est fait.", jsonable_encoder(pending_result)


def _handler_accepts_debug_kwarg(handler: Any) -> bool:
    """Return True when handler supports a `debug` keyword argument."""

    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):
        return False

    parameters = signature.parameters
    if "debug" in parameters:
        return True

    return any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values())


def _handler_accepts_global_state_kwarg(handler: Any) -> bool:
    """Return True when handler supports a `global_state` keyword argument."""

    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):
        return False

    parameters = signature.parameters
    if "global_state" in parameters:
        return True

    return any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values())


def _is_profile_field_completed(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _compute_bootstrap_global_state(profile_fields: dict[str, Any]) -> dict[str, Any]:
    """Compute initial global state from profile completeness."""

    is_profile_complete = all(
        _is_profile_field_completed(profile_fields.get(field_name))
        for field_name in _PROFILE_COMPLETION_FIELDS
    )
    if is_profile_complete:
        return {
            "mode": "onboarding",
            "onboarding_step": "profile",
            "onboarding_substep": "profile_confirm",
            "profile_confirmed": False,
            "bank_accounts_confirmed": False,
            "has_bank_accounts": False,
            "has_imported_transactions": False,
            "budget_created": False,
        }
    return {
        "mode": "onboarding",
        "onboarding_step": "profile",
        "onboarding_substep": "profile_collect",
        "profile_confirmed": False,
        "bank_accounts_confirmed": False,
        "has_bank_accounts": False,
        "has_imported_transactions": False,
        "budget_created": False,
    }


def _is_valid_global_state(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    mode = value.get("mode")
    onboarding_step = value.get("onboarding_step")
    if mode not in _GLOBAL_STATE_MODES:
        return False
    if onboarding_step not in _GLOBAL_STATE_ONBOARDING_STEPS:
        return False
    onboarding_substep = value.get("onboarding_substep")
    if onboarding_substep not in _GLOBAL_STATE_ONBOARDING_SUBSTEPS:
        return False
    has_bank_accounts = value.get("has_bank_accounts")
    if has_bank_accounts is not None and not isinstance(has_bank_accounts, bool):
        return False
    for flag_name in ("profile_confirmed", "bank_accounts_confirmed"):
        flag_value = value.get(flag_name)
        if flag_value is not None and not isinstance(flag_value, bool):
            return False
    return True


def _is_profile_complete(profile_fields: dict[str, Any]) -> bool:
    """Return True when onboarding profile completion fields are all present."""

    return all(
        _is_profile_field_completed(profile_fields.get(field_name))
        for field_name in _PROFILE_COMPLETION_FIELDS
    )


def _normalize_onboarding_step_substep(global_state: dict[str, Any]) -> dict[str, Any]:
    """Normalize inconsistent onboarding step/substep combinations."""

    if global_state.get("mode") != "onboarding":
        return global_state

    normalized = dict(global_state)
    step = normalized.get("onboarding_step")
    substep = normalized.get("onboarding_substep")

    if step is None:
        normalized["onboarding_substep"] = None
        return normalized

    valid_substeps_by_step = {
        "profile": {"profile_collect", "profile_confirm"},
        "bank_accounts": {"bank_accounts_collect", "bank_accounts_confirm"},
        "import": {"import_select_account"},
        "categories": {"categories_intro", "categories_bootstrap", "categories_review"},
        "report": {"report_offer", "report_sent"},
    }
    default_substep_by_step = {
        "profile": "profile_collect",
        "bank_accounts": "bank_accounts_collect",
        "import": "import_select_account",
        "categories": "categories_bootstrap",
        "report": "report_offer",
    }

    if step == "categories" and substep == "categories_intro":
        normalized["onboarding_substep"] = "categories_bootstrap"
        return normalized

    if step in valid_substeps_by_step:
        if substep not in valid_substeps_by_step[step]:
            normalized["onboarding_substep"] = default_substep_by_step[step]
        return normalized

    if step in {"categories", "budget", "report"}:
        normalized["onboarding_substep"] = None

    return normalized


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip().lower()).encode("ascii", "ignore").decode("ascii")
    return " ".join(normalized.split())


_MERCHANT_NOISE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"paiement.*", flags=re.IGNORECASE),
    re.compile(r"debit.*", flags=re.IGNORECASE),
    re.compile(r"cr[eé]dit.*", flags=re.IGNORECASE),
    re.compile(r"no de transaction.*", flags=re.IGNORECASE),
    re.compile(r"transaction.*", flags=re.IGNORECASE),
    re.compile(r"motif.*", flags=re.IGNORECASE),
    re.compile(r"twint.*", flags=re.IGNORECASE),
    re.compile(r"ubs.*", flags=re.IGNORECASE),
)
_MERCHANT_LONG_NUMBER_TOKEN = re.compile(r"\b\S*\d{6,}\S*\b")
_MERCHANT_GENERIC_TOKENS = {
    "paiement",
    "debit",
    "credit",
    "carte",
    "twint",
    "motif",
    "transaction",
    "ubst",
    "ubs",
    "mobile",
    "sa",
    "ag",
    "sarl",
    "gmbh",
    "ltd",
    "inc",
    "co",
}
_MERCHANT_STOPWORDS = {
    "le",
    "la",
    "les",
    "de",
    "du",
    "des",
    "d",
    "a",
    "au",
    "aux",
    "chez",
    "route",
    "rte",
    "na",
    "no",
    "numero",
    "num",
    "sa",
    "ag",
    "sarl",
    "gmbh",
    "ltd",
    "inc",
    "co",
    "compagnie",
    "caisse",
    "solde",
}
_MERCHANT_GENERIC_HEADS = {
    "restaurant",
    "station",
    "stationservice",
    "station-service",
    "boulangerie",
    "boucherie",
    "caisse",
    "assurance",
    "paybyphone",
    "parking",
    "marche",
    "atelier",
    "portes",
    "solde",
    "decompte",
    "sante",
    "banque",
}
_MERCHANT_ALLOWED_SHORT_TOKENS = {"sbb", "ubs", "avs"}
_MERCHANT_KNOWN_ACRONYMS = {"sbb", "ubs", "avs"}
_MERCHANT_SUSPECT_FIRST_NAMES = {
    "tristan",
    "alex",
    "alexandre",
    "antoine",
    "benjamin",
    "christophe",
    "daniel",
    "david",
    "jerome",
    "julien",
    "kevin",
    "luc",
    "marc",
    "martin",
    "mathieu",
    "michael",
    "nicolas",
    "olivier",
    "pierre",
    "samuel",
    "sebastien",
    "thomas",
}


def _canonicalize_merchant(candidate: str) -> tuple[str, str, str] | None:
    candidate_raw = candidate.strip()
    if not candidate_raw:
        return None

    alias_raw = " ".join(candidate_raw.split())
    candidate_work = alias_raw.split(";", maxsplit=1)[0]
    candidate_work = candidate_work.split(",", maxsplit=1)[0]
    candidate_work = _MERCHANT_LONG_NUMBER_TOKEN.sub(" ", candidate_work)
    for pattern in _MERCHANT_NOISE_PATTERNS:
        candidate_work = pattern.sub("", candidate_work)
    candidate_work = candidate_work.strip(" .,:;-_/\\")
    candidate_work = " ".join(candidate_work.split())

    base_norm = _normalize_text(candidate_work)
    if len(base_norm) < 2:
        return None

    if "coop" in base_norm or base_norm.startswith("coop-"):
        return ("Coop", "coop", alias_raw)
    if "migrolino" in base_norm:
        return ("Migrolino", "migrolino", alias_raw)
    if "migrol" in base_norm:
        return ("Migrol", "migrol", alias_raw)
    if "migros" in base_norm:
        return ("Migros", "migros", alias_raw)
    if "denner" in base_norm:
        return ("Denner", "denner", alias_raw)
    if "lidl" in base_norm:
        return ("Lidl", "lidl", alias_raw)
    if "aldi" in base_norm:
        return ("Aldi", "aldi", alias_raw)
    if "sbb" in base_norm:
        return ("SBB", "sbb", alias_raw)
    if "tamoil" in base_norm:
        return ("Tamoil", "tamoil", alias_raw)
    if "decathlon" in base_norm:
        return ("Decathlon", "decathlon", alias_raw)
    if "sumup" in base_norm:
        return ("SumUp", "sumup", alias_raw)
    if "swisscaution" in base_norm:
        return ("SwissCaution", "swisscaution", alias_raw)

    all_tokens = [token for token in base_norm.split() if token]
    if not all_tokens:
        return (alias_raw[:64], base_norm[:64], alias_raw)

    first_token = all_tokens[0]
    if len(all_tokens) >= 2 and first_token in _MERCHANT_SUSPECT_FIRST_NAMES:
        all_tokens = all_tokens[1:]

    def _filter_tokens(tokens: list[str]) -> list[str]:
        filtered: list[str] = []
        for token in tokens:
            cleaned_token = re.sub(r"[^a-z0-9]", "", token)
            if not cleaned_token:
                continue
            if (
                cleaned_token in _MERCHANT_GENERIC_TOKENS
                or cleaned_token in _MERCHANT_STOPWORDS
                or cleaned_token in _MERCHANT_GENERIC_HEADS
            ):
                continue
            if cleaned_token.isnumeric():
                continue
            if len(cleaned_token) < 4 and cleaned_token not in _MERCHANT_ALLOWED_SHORT_TOKENS:
                continue
            if cleaned_token == "xxxx":
                continue
            filtered.append(cleaned_token)
        return filtered

    first_cleaned_token = re.sub(r"[^a-z0-9]", "", all_tokens[0]) if all_tokens else ""
    if first_cleaned_token in _MERCHANT_GENERIC_HEADS:
        filtered_tokens = _filter_tokens(all_tokens[1:])
    else:
        filtered_tokens = _filter_tokens(all_tokens)

    if filtered_tokens:
        selected_tokens = filtered_tokens[:3]
        name_norm = " ".join(selected_tokens)
        display_name = " ".join(
            token.upper() if token in _MERCHANT_KNOWN_ACRONYMS else token[:1].upper() + token[1:]
            for token in selected_tokens
        )
        return (display_name[:64], name_norm[:64], alias_raw)

    return (alias_raw[:64], base_norm[:64], alias_raw)


def _is_yes(message: str) -> bool:
    return _normalize_text(message) in _YES_VALUES


def _is_no(message: str) -> bool:
    return _normalize_text(message) in _NO_VALUES


def _pick_category_for_merchant_name(name: str) -> str:
    normalized_name = _normalize_text(name)
    for keywords, category_name in _MERCHANT_CATEGORY_RULES:
        if any(keyword in normalized_name for keyword in keywords):
            return category_name
    return _FALLBACK_MERCHANT_CATEGORY


def _bootstrap_merchants_from_imported_releves(
    *,
    profiles_repository: ProfilesRepository,
    profile_id: UUID,
    limit: int = 500,
) -> dict[str, int]:
    """Best-effort deterministic merchant entity matching from imported statements."""

    rows = profiles_repository.list_releves_without_merchant(profile_id=profile_id, limit=limit)
    categories_rows = []
    if hasattr(profiles_repository, "list_profile_categories"):
        categories_rows = profiles_repository.list_profile_categories(profile_id=profile_id)
    categories_by_key: dict[str, dict[str, Any]] = {}
    for row in categories_rows:
        system_key_norm = _normalize_text(str(row.get("system_key") or ""))
        if system_key_norm:
            categories_by_key[system_key_norm] = row
        name_norm_norm = _normalize_text(str(row.get("name_norm") or ""))
        if name_norm_norm:
            categories_by_key[name_norm_norm] = row
    processed_count = 0
    linked_count = 0
    skipped_count = 0
    suggestion_rows: list[dict[str, Any]] = []
    seen_pending_alias_norms: set[str] = set()
    suggestions_created_count = 0

    for row in rows:
        processed_count += 1
        payee = " ".join(str(row.get("payee") or "").split())
        libelle = " ".join(str(row.get("libelle") or "").split())
        observed_alias = (
            extract_observed_alias_from_label(payee)
            or payee
            or extract_observed_alias_from_label(libelle)
            or libelle
        )
        releve_id_raw = row.get("id")

        if not observed_alias or not releve_id_raw:
            skipped_count += 1
            continue
        observed_alias_norm = _normalize_text(observed_alias)
        if not observed_alias_norm:
            skipped_count += 1
            continue

        try:
            releve_id = UUID(str(releve_id_raw))
            entity = profiles_repository.find_merchant_entity_by_alias_norm(alias_norm=observed_alias_norm)
            if not entity:
                if observed_alias_norm not in seen_pending_alias_norms:
                    suggestion_rows.append(
                        {
                            "status": "pending",
                            "action": "map_alias",
                            "observed_alias": observed_alias,
                            "observed_alias_norm": observed_alias_norm,
                            "suggested_entity_name": None,
                            "confidence": None,
                            "rationale": "unknown alias from import",
                        }
                    )
                    seen_pending_alias_norms.add(observed_alias_norm)
                skipped_count += 1
                continue

            entity_id = UUID(str(entity["id"]))
            category_id: UUID | None = None
            override = profiles_repository.get_profile_merchant_override(
                profile_id=profile_id,
                merchant_entity_id=entity_id,
            )
            override_category_id = override.get("category_id") if isinstance(override, dict) else None
            if override_category_id:
                category_id = UUID(str(override_category_id))
            else:
                suggested_key = _normalize_text(str(entity.get("suggested_category_norm") or ""))
                matched_category = categories_by_key.get(suggested_key)
                if matched_category and matched_category.get("id"):
                    category_id = UUID(str(matched_category["id"]))

            profiles_repository.attach_merchant_entity_to_releve(
                releve_id=releve_id,
                merchant_entity_id=entity_id,
                category_id=category_id,
            )
            profiles_repository.upsert_merchant_alias(
                merchant_entity_id=entity_id,
                alias=observed_alias,
                alias_norm=observed_alias_norm,
                source="import",
            )
            if category_id is not None:
                profiles_repository.upsert_profile_merchant_override(
                    profile_id=profile_id,
                    merchant_entity_id=entity_id,
                    category_id=category_id,
                    status="auto",
                )
            linked_count += 1
        except Exception:
            skipped_count += 1
            logger.exception(
                "import_releves_entity_link_failed profile_id=%s releve_id=%s",
                profile_id,
                releve_id_raw,
            )

    if suggestion_rows:
        try:
            if hasattr(profiles_repository, "create_map_alias_suggestions"):
                suggestions_created_count = int(
                    profiles_repository.create_map_alias_suggestions(profile_id=profile_id, rows=suggestion_rows)
                    or 0
                )
        except Exception:
            logger.exception("import_releves_map_alias_suggestions_failed profile_id=%s", profile_id)

    logger.info(
        "import_releves_entity_link_summary profile_id=%s processed=%s linked=%s skipped=%s suggestions_created_count=%s",
        profile_id,
        processed_count,
        linked_count,
        skipped_count,
        suggestions_created_count,
    )
    return {
        "processed_count": processed_count,
        "linked_count": linked_count,
        "skipped_count": skipped_count,
        "suggestions_created_count": suggestions_created_count,
    }


def _format_accounts_for_reply(accounts: list[dict[str, Any]]) -> str:
    names = [str(account.get("name", "")).strip() for account in accounts]
    filtered_names = [name for name in names if name]
    return ", ".join(filtered_names) if filtered_names else "aucun compte"


def _match_bank_account_name(message: str, accounts: list[dict[str, Any]]) -> dict[str, Any] | None:
    normalized_message = _normalize_text(message)
    cleaned_message = normalized_message.replace("compte bancaire", "").replace("compte", "")
    cleaned_message = " ".join(cleaned_message.split())

    for account in accounts:
        account_name = str(account.get("name", ""))
        normalized_name = _normalize_text(account_name)
        if cleaned_message == normalized_name or normalized_message == normalized_name:
            return account
    return None


def _build_onboarding_reminder(global_state: dict[str, Any] | None) -> str | None:
    if not isinstance(global_state, dict) or global_state.get("mode") != "onboarding":
        return None

    substep = global_state.get("onboarding_substep")
    if substep == "profile_collect":
        return "(Pour continuer l’onboarding : réponds aux informations demandées.)"
    if substep == "profile_confirm":
        return "(Pour continuer l’onboarding : réponds OUI/NON pour confirmer le profil.)"
    if substep == "bank_accounts_collect":
        return "(Pour continuer l’onboarding : indique les banques à ajouter.)"
    if substep == "bank_accounts_confirm":
        return "(Pour continuer l’onboarding : réponds OUI/NON à la question sur les comptes.)"
    if substep == "import_select_account":
        return "(Pour continuer : indique le compte à importer.)"
    if substep == "categories_intro":
        return "(Pour continuer l’onboarding : démarrons le bootstrap des catégories.)"
    if substep == "categories_bootstrap":
        return "(Pour continuer l’onboarding : je prépare automatiquement les catégories et les marchands.)"
    if substep == "categories_review":
        return "(Pour continuer l’onboarding : réponds OUI/NON pour afficher ton rapport de dépenses.)"
    if substep == "report_offer":
        return "(Pour continuer l’onboarding : réponds OUI/NON pour ouvrir le rapport PDF.)"
    return None


def _extract_name_from_message(message: str) -> tuple[str, str] | None:
    match = _ONBOARDING_NAME_PATTERN.match(message)
    if not match:
        return None
    first_name, last_name = match.groups()
    return first_name, last_name


def _extract_name_from_text_prefix(message: str) -> tuple[str, str] | None:
    match = _ONBOARDING_NAME_PREFIX_PATTERN.match(message)
    if not match:
        return None

    remaining_text = message[match.end() :].strip()
    if remaining_text and _extract_birth_date_from_text(remaining_text) is None:
        return None

    first_name, last_name = match.groups()
    return first_name, last_name


def _extract_birth_date_from_message(message: str) -> str | None:
    normalized = message.strip().lower()

    year: int
    month: int
    day: int

    iso_match = _ONBOARDING_BIRTH_DATE_PATTERN.match(normalized)
    if iso_match:
        year, month, day = (int(chunk) for chunk in iso_match.groups())
    else:
        dot_match = _ONBOARDING_BIRTH_DATE_DOT_PATTERN.match(normalized)
        slash_match = _ONBOARDING_BIRTH_DATE_SLASH_PATTERN.match(normalized)
        month_name_match = _ONBOARDING_BIRTH_DATE_MONTH_NAME_PATTERN.match(
            unicodedata.normalize("NFKD", normalized).encode("ascii", "ignore").decode("ascii")
        )

        if dot_match:
            day, month, year = (int(chunk) for chunk in dot_match.groups())
        elif slash_match:
            day, month, year = (int(chunk) for chunk in slash_match.groups())
        elif month_name_match:
            day_raw, month_raw, year_raw = month_name_match.groups()
            mapped_month = _FRENCH_MONTH_TO_NUMBER.get(month_raw)
            if mapped_month is None:
                return None

            day = int(day_raw)
            month = mapped_month
            year = int(year_raw)
        else:
            return None

    try:
        parsed = date(year=year, month=month, day=day)
    except ValueError:
        return None

    return parsed.isoformat()


def _extract_birth_date_from_text(message: str) -> str | None:
    for pattern in _ONBOARDING_BIRTH_DATE_IN_TEXT_PATTERNS:
        match = pattern.search(message)
        if not match:
            continue
        parsed_birth_date = _extract_birth_date_from_message(match.group(0))
        if parsed_birth_date is not None:
            return parsed_birth_date
    return None


def _build_onboarding_global_state(
    existing_global_state: dict[str, Any] | None,
    *,
    onboarding_step: str = "profile",
    onboarding_substep: str | None = "profile_collect",
) -> dict[str, Any]:
    return {
        "mode": "onboarding",
        "onboarding_step": onboarding_step,
        "onboarding_substep": onboarding_substep,
        "profile_confirmed": bool(
            isinstance(existing_global_state, dict) and existing_global_state.get("profile_confirmed", False)
        ),
        "bank_accounts_confirmed": bool(
            isinstance(existing_global_state, dict) and existing_global_state.get("bank_accounts_confirmed", False)
        ),
        "has_bank_accounts": bool(
            isinstance(existing_global_state, dict) and existing_global_state.get("has_bank_accounts", False)
        ),
        "has_imported_transactions": bool(
            isinstance(existing_global_state, dict)
            and existing_global_state.get("has_imported_transactions", False)
        ),
        "budget_created": bool(
            isinstance(existing_global_state, dict) and existing_global_state.get("budget_created", False)
        ),
    }


def _build_free_chat_global_state(existing_global_state: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "mode": "free_chat",
        "onboarding_step": None,
        "onboarding_substep": None,
        "profile_confirmed": bool(
            isinstance(existing_global_state, dict) and existing_global_state.get("profile_confirmed", False)
        ),
        "bank_accounts_confirmed": bool(
            isinstance(existing_global_state, dict) and existing_global_state.get("bank_accounts_confirmed", False)
        ),
        "has_bank_accounts": bool(
            isinstance(existing_global_state, dict) and existing_global_state.get("has_bank_accounts", False)
        ),
        "has_imported_transactions": bool(
            isinstance(existing_global_state, dict)
            and existing_global_state.get("has_imported_transactions", False)
        ),
        "budget_created": bool(
            isinstance(existing_global_state, dict) and existing_global_state.get("budget_created", False)
        ),
    }


def _build_bank_accounts_onboarding_global_state(
    existing_global_state: dict[str, Any] | None,
    *,
    onboarding_substep: str = "bank_accounts_collect",
) -> dict[str, Any]:
    return _build_onboarding_global_state(
        existing_global_state,
        onboarding_step="bank_accounts",
        onboarding_substep=onboarding_substep,
    )




def _has_any_bank_accounts(profiles_repository: Any, profile_id: UUID) -> bool | None:
    """Return bank account presence when check is supported, else None."""

    if not hasattr(profiles_repository, "list_bank_accounts"):
        return None
    try:
        bank_accounts = profiles_repository.list_bank_accounts(profile_id=profile_id)
    except Exception:
        logger.exception("failed_to_list_bank_accounts_for_re_gate profile_id=%s", profile_id)
        return None
    return bool(bank_accounts)


def _get_profile_fields_safe(profiles_repository: Any, profile_id: UUID) -> dict[str, Any] | None:
    """Return onboarding profile fields when repository supports it, else None."""

    if not hasattr(profiles_repository, "get_profile_fields"):
        return None
    try:
        profile_fields = profiles_repository.get_profile_fields(
            profile_id=profile_id,
            fields=list(_PROFILE_COMPLETION_FIELDS),
        )
    except Exception:
        logger.exception("failed_to_get_profile_fields_for_re_gate profile_id=%s", profile_id)
        return None
    return dict(profile_fields) if isinstance(profile_fields, dict) else {}


def _has_complete_profile(profiles_repository: Any, profile_id: UUID) -> bool | None:
    """Return profile completion status when supported, else None."""

    profile_fields = _get_profile_fields_safe(profiles_repository, profile_id)
    if profile_fields is None:
        return None
    return _is_profile_complete(profile_fields)



class ChatRequest(BaseModel):
    """Incoming chat request payload."""

    message: str
    request_greeting: bool = False


class ChatResponse(BaseModel):
    """Outgoing chat response payload."""

    reply: str
    tool_result: Any | None
    plan: Any | None = None


class ImportFilePayload(BaseModel):
    """Single file payload for bank statement import."""

    filename: str
    content_base64: str


class ImportRequestPayload(BaseModel):
    """Import request payload sent by the UI."""

    files: list[ImportFilePayload]
    bank_account_id: str | None = None
    import_mode: str = "analyze"
    modified_action: str = "replace"


class HardResetPayload(BaseModel):
    """Payload for debug hard reset endpoint."""

    confirm: bool = False


class RenameMerchantPayload(BaseModel):
    """Payload for merchant rename endpoint."""

    merchant_id: UUID
    name: str


class MergeMerchantsPayload(BaseModel):
    """Payload for merchant merge endpoint."""

    source_merchant_id: UUID
    target_merchant_id: UUID


class MerchantSuggestionsListPayload(BaseModel):
    """Payload for merchant suggestions listing endpoint."""

    status: str = "pending"
    limit: int = 50


class MerchantSuggestionApplyPayload(BaseModel):
    """Payload for merchant suggestion apply endpoint."""

    suggestion_id: UUID


class MerchantAliasResolvePayload(BaseModel):
    """Payload for map_alias suggestion batch resolver endpoint."""

    limit: int = 100


class ResolvePendingMerchantAliasesPayload(BaseModel):
    """Payload for manual pending map_alias suggestions resolution endpoint."""

    limit: int | None = None
    max_batches: int | None = None


@lru_cache(maxsize=1)
def get_tool_router() -> ToolRouter:
    """Create and cache the tool router once per process."""

    backend_tool_service = build_backend_tool_service()
    backend_client = BackendClient(tool_service=backend_tool_service)
    return ToolRouter(backend_client=backend_client)


@lru_cache(maxsize=1)
def get_agent_loop() -> AgentLoop:
    """Create and cache the agent loop once per process."""
    backend_tool_service = build_backend_tool_service()
    backend_client = BackendClient(tool_service=backend_tool_service)
    tool_router = ToolRouter(backend_client=backend_client)
    llm_planner: LLMPlanner | None = None

    if _config.llm_enabled():
        llm_planner = LLMPlanner(strict=_config.llm_strict())

    loop = AgentLoop(
        tool_router=tool_router,
        llm_planner=llm_planner,
    )
    logger.info("using_agent_loop=%s.%s", loop.__class__.__module__, loop.__class__.__name__)
    return loop


@lru_cache(maxsize=1)
def get_profiles_repository() -> SupabaseProfilesRepository:
    """Create and cache profiles repository."""

    supabase_url = _config.supabase_url()
    service_role_key = _config.supabase_service_role_key()
    anon_key = _config.supabase_anon_key()
    if not supabase_url or not service_role_key:
        raise RuntimeError("Supabase backend is not configured")

    client = SupabaseClient(
        settings=SupabaseSettings(
            url=supabase_url,
            service_role_key=service_role_key,
            anon_key=anon_key,
        )
    )
    return SupabaseProfilesRepository(client)


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="Invalid Authorization header")
    token = authorization[len(prefix) :].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    return token


def _resolve_authenticated_profile(authorization: str | None) -> tuple[UUID, UUID]:
    """Resolve authenticated user and linked profile from authorization header."""

    token = _extract_bearer_token(authorization)
    try:
        user_payload = get_user_from_bearer_token(token)
    except UnauthorizedError as exc:
        raise HTTPException(status_code=401, detail="Unauthorized") from exc

    user_id = user_payload.get("id")
    if not isinstance(user_id, str):
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        auth_user_id = UUID(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Unauthorized") from exc

    email_value = user_payload.get("email")
    email = email_value if isinstance(email_value, str) else None

    profiles_repository = get_profiles_repository()
    profile_id = profiles_repository.get_profile_id_for_auth_user(
        auth_user_id=auth_user_id,
        email=email,
    )
    if profile_id is None:
        raise HTTPException(
            status_code=401,
            detail="No profile linked to authenticated user (by account_id or email)",
        )
    return auth_user_id, profile_id


app = FastAPI(title="IA Financial Assistant Agent API")

ALLOW_ORIGINS = _config.cors_allow_origins()


@app.middleware("http")
async def log_http_requests(request: Request, call_next):
    """Log incoming requests, HTTP status codes and unexpected errors."""

    logger.info("http_request_received method=%s path=%s", request.method, request.url.path)
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "http_request_failed method=%s path=%s",
            request.method,
            request.url.path,
        )
        raise

    logger.info(
        "http_response_sent method=%s path=%s status_code=%s",
        request.method,
        request.url.path,
        response.status_code,
    )
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger.info("cors_allow_origins=%s", ALLOW_ORIGINS)


@app.exception_handler(Exception)
async def handle_unexpected_exception(request: Request, exc: Exception) -> JSONResponse:
    """Return a JSON 500 response for unhandled exceptions."""

    logger.exception(
        "unhandled_exception method=%s path=%s exception_type=%s message=%s",
        request.method,
        request.url.path,
        type(exc).__name__,
        str(exc),
        exc_info=exc,
    )
    return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})


@app.get("/health")
def health() -> dict[str, str]:
    """Healthcheck endpoint."""

    return {"status": "ok"}


@app.post("/agent/chat", response_model=ChatResponse)
def agent_chat(
    payload: ChatRequest,
    authorization: str | None = Header(default=None),
    x_debug: str | None = Header(default=None),
) -> ChatResponse:
    """Handle a user chat message through the agent loop."""

    logger.info("agent_chat_received message_length=%s", len(payload.message))
    profile_id: UUID | None = None
    try:
        auth_user_id, profile_id = _resolve_authenticated_profile(authorization)
        profiles_repository = get_profiles_repository()

        chat_state = profiles_repository.get_chat_state(profile_id=profile_id, user_id=auth_user_id)
        active_task = chat_state.get("active_task") if isinstance(chat_state, dict) else None
        state = chat_state.get("state") if isinstance(chat_state, dict) else None
        state_dict = dict(state) if isinstance(state, dict) else None
        existing_global_state = state_dict.get("global_state") if isinstance(state_dict, dict) else None
        global_state = existing_global_state if _is_valid_global_state(existing_global_state) else None
        should_persist_global_state = False
        mode = global_state.get("mode") if _is_valid_global_state(global_state) else None
        onboarding_step = global_state.get("onboarding_step") if _is_valid_global_state(global_state) else None
        if _is_valid_global_state(global_state):
            normalized = _normalize_onboarding_step_substep(global_state)
            if normalized != global_state:
                global_state = normalized
                state_dict = dict(state_dict) if isinstance(state_dict, dict) else {}
                state_dict["global_state"] = normalized
                should_persist_global_state = True

        if global_state is None and hasattr(profiles_repository, "get_profile_fields"):
            try:
                profile_fields = profiles_repository.get_profile_fields(
                    profile_id=profile_id,
                    fields=list(_PROFILE_COMPLETION_FIELDS),
                )
            except Exception:
                logger.exception("global_state_bootstrap_profile_lookup_failed profile_id=%s", profile_id)
                profile_fields = {}
            global_state = _compute_bootstrap_global_state(profile_fields)
            state_dict = dict(state_dict) if isinstance(state_dict, dict) else {}
            state_dict["global_state"] = global_state
            should_persist_global_state = True

        profile_complete = _has_complete_profile(profiles_repository, profile_id)
        current_mode = global_state.get("mode") if _is_valid_global_state(global_state) else None
        current_step = global_state.get("onboarding_step") if _is_valid_global_state(global_state) else None
        current_substep = global_state.get("onboarding_substep") if _is_valid_global_state(global_state) else None
        should_force_profile_re_gate = not (
            current_mode == "onboarding"
            and current_step == "profile"
            and current_substep == "profile_collect"
        )
        if profile_complete is False and should_force_profile_re_gate:
            updated_global_state = _build_onboarding_global_state(
                global_state if _is_valid_global_state(global_state) else None,
                onboarding_step="profile",
                onboarding_substep="profile_collect",
            )
            updated_global_state["profile_confirmed"] = False
            if _is_valid_global_state(updated_global_state):
                updated_global_state = _normalize_onboarding_step_substep(updated_global_state)
            state_dict = dict(state_dict) if isinstance(state_dict, dict) else {}
            state_dict["global_state"] = updated_global_state
            updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
            updated_chat_state["state"] = state_dict
            profiles_repository.update_chat_state(
                profile_id=profile_id,
                user_id=auth_user_id,
                chat_state=updated_chat_state,
            )
            return ChatResponse(
                reply=(
                    "Avant de continuer, quel est ton prénom et ton nom ? "
                    "(ex: Paul Murt)"
                ),
                tool_result=None,
                plan=None,
            )

        is_onboarding_profile_collect = (
            _is_valid_global_state(global_state)
            and global_state.get("mode") == "onboarding"
            and global_state.get("onboarding_step") == "profile"
            and global_state.get("onboarding_substep") == "profile_collect"
        )
        if payload.request_greeting and is_onboarding_profile_collect:
            return ChatResponse(
                reply=(
                    "Salut 🙂 Je vais te poser 2–3 infos pour créer ton profil, "
                    "puis on importera ton premier relevé bancaire (CSV)."
                ),
                tool_result=None,
                plan=None,
            )

        if _is_valid_global_state(global_state):
            state_dict = dict(state_dict) if isinstance(state_dict, dict) else {}

            if global_state.get("mode") == "onboarding" and global_state.get("onboarding_step") == "profile" and hasattr(
                profiles_repository, "get_profile_fields"
            ):
                try:
                    profile_fields = profiles_repository.get_profile_fields(
                        profile_id=profile_id,
                        fields=list(_PROFILE_COMPLETION_FIELDS),
                    )
                except Exception:
                    logger.exception("onboarding_profile_lookup_failed profile_id=%s", profile_id)
                    profile_fields = {}
                substep = global_state.get("onboarding_substep")
                if substep is None:
                    substep = "profile_confirm" if _is_profile_complete(profile_fields) else "profile_collect"

                if substep == "profile_collect":
                    message = payload.message.strip()
                    if hasattr(profiles_repository, "update_profile_fields"):
                        extracted_name = _extract_name_from_text_prefix(message) or _extract_name_from_message(message)
                        if extracted_name is not None:
                            first_name, last_name = extracted_name
                            profiles_repository.update_profile_fields(
                                profile_id=profile_id,
                                set_dict={"first_name": first_name, "last_name": last_name},
                            )
                            try:
                                profile_fields = profiles_repository.get_profile_fields(
                                    profile_id=profile_id,
                                    fields=list(_PROFILE_COMPLETION_FIELDS),
                                )
                            except Exception:
                                logger.exception(
                                    "onboarding_profile_refetch_after_name_update_failed profile_id=%s",
                                    profile_id,
                                )
                                profile_fields = {}

                        extracted_birth_date = _extract_birth_date_from_text(message) or _extract_birth_date_from_message(
                            message
                        )
                        if extracted_birth_date is not None:
                            profiles_repository.update_profile_fields(
                                profile_id=profile_id,
                                set_dict={"birth_date": extracted_birth_date},
                            )
                            try:
                                profile_fields = profiles_repository.get_profile_fields(
                                    profile_id=profile_id,
                                    fields=list(_PROFILE_COMPLETION_FIELDS),
                                )
                            except Exception:
                                logger.exception(
                                    "onboarding_profile_refetch_after_birth_date_update_failed profile_id=%s",
                                    profile_id,
                                )
                                profile_fields = {}

                    first_name = str(profile_fields.get("first_name", "")).strip()
                    last_name = str(profile_fields.get("last_name", "")).strip()
                    has_name = _is_profile_field_completed(profile_fields.get("first_name")) and _is_profile_field_completed(
                        profile_fields.get("last_name")
                    )
                    has_birth_date = _is_profile_field_completed(profile_fields.get("birth_date"))

                    if has_name and has_birth_date:
                        updated_global_state = _build_onboarding_global_state(
                            global_state,
                            onboarding_step="bank_accounts",
                            onboarding_substep="bank_accounts_collect",
                        )
                        updated_global_state["profile_confirmed"] = True
                        state_dict["global_state"] = updated_global_state
                        updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                        updated_chat_state["state"] = state_dict
                        profiles_repository.update_chat_state(
                            profile_id=profile_id,
                            user_id=auth_user_id,
                            chat_state=updated_chat_state,
                        )
                        return ChatResponse(
                            reply=(
                                "Parfait ✅\n"
                                "Tu veux ajouter quels comptes bancaires ? (ex: UBS, Revolut)"
                            ),
                            tool_result=None,
                            plan=None,
                        )

                    if not has_name:
                        reply = "Ton prénom et ton nom ?"
                    else:
                        reply = "Merci ! Ta date de naissance ? (ex: 2002-01-10)"

                    updated_global_state = _build_onboarding_global_state(
                        global_state,
                        onboarding_step="profile",
                        onboarding_substep="profile_collect",
                    )
                    state_dict["global_state"] = updated_global_state
                    updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                    updated_chat_state["state"] = state_dict
                    profiles_repository.update_chat_state(
                        profile_id=profile_id,
                        user_id=auth_user_id,
                        chat_state=updated_chat_state,
                    )
                    return ChatResponse(reply=reply, tool_result=None, plan=None)

                if substep == "profile_confirm":
                    if _is_yes(payload.message):
                        updated_global_state = _build_bank_accounts_onboarding_global_state(
                            {
                                **global_state,
                                "profile_confirmed": True,
                            },
                            onboarding_substep="bank_accounts_collect",
                        )
                        updated_global_state["profile_confirmed"] = True
                        state_dict["global_state"] = updated_global_state
                        updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                        updated_chat_state["state"] = state_dict
                        profiles_repository.update_chat_state(
                            profile_id=profile_id,
                            user_id=auth_user_id,
                            chat_state=updated_chat_state,
                        )
                        return ChatResponse(
                            reply="Tu veux ajouter quels comptes bancaires ? (ex: UBS, Revolut)",
                            tool_result=None,
                            plan=None,
                        )
                    if _is_no(payload.message):
                        updated_global_state = _build_onboarding_global_state(
                            {
                                **global_state,
                                "profile_confirmed": False,
                            },
                            onboarding_step="profile",
                            onboarding_substep="profile_collect",
                        )
                        updated_global_state["profile_confirmed"] = False
                        state_dict["global_state"] = updated_global_state
                        updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                        updated_chat_state["state"] = state_dict
                        profiles_repository.update_chat_state(
                            profile_id=profile_id,
                            user_id=auth_user_id,
                            chat_state=updated_chat_state,
                        )
                        return ChatResponse(
                            reply="Ok, qu’est-ce qui est incorrect ? (prénom / nom / date de naissance)",
                            tool_result=None,
                            plan=None,
                        )
                    updated_global_state = _build_onboarding_global_state(
                        global_state,
                        onboarding_step="profile",
                        onboarding_substep="profile_confirm",
                    )
                    state_dict["global_state"] = updated_global_state
                    updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                    updated_chat_state["state"] = state_dict
                    profiles_repository.update_chat_state(
                        profile_id=profile_id,
                        user_id=auth_user_id,
                        chat_state=updated_chat_state,
                    )
                    return ChatResponse(reply="Réponds OUI ou NON.", tool_result=None, plan=None)

            mode = global_state.get("mode")
            onboarding_step = global_state.get("onboarding_step")
            has_bank_accounts = _has_any_bank_accounts(profiles_repository, profile_id)

            if mode == "free_chat" and has_bank_accounts is False:
                updated_global_state = _build_bank_accounts_onboarding_global_state(
                    global_state,
                    onboarding_substep="bank_accounts_collect",
                )
                updated_global_state["has_bank_accounts"] = False
                state_dict["global_state"] = updated_global_state
                updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                updated_chat_state["state"] = state_dict
                profiles_repository.update_chat_state(
                    profile_id=profile_id,
                    user_id=auth_user_id,
                    chat_state=updated_chat_state,
                )
                return ChatResponse(
                    reply="Avant de continuer, indique-moi ta/tes banques (ex: ‘UBS, Revolut’).",
                    tool_result=None,
                    plan=None,
                )

            if mode == "onboarding" and onboarding_step in {"import", "categories", "budget"} and has_bank_accounts is False:
                updated_global_state = _build_bank_accounts_onboarding_global_state(
                    global_state,
                    onboarding_substep="bank_accounts_collect",
                )
                updated_global_state["has_bank_accounts"] = False
                state_dict["global_state"] = updated_global_state
                updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                updated_chat_state["state"] = state_dict
                profiles_repository.update_chat_state(
                    profile_id=profile_id,
                    user_id=auth_user_id,
                    chat_state=updated_chat_state,
                )
                return ChatResponse(
                    reply="Avant de continuer, indique-moi ta/tes banques (ex: ‘UBS, Revolut’).",
                    tool_result=None,
                    plan=None,
                )

            should_re_gate_import = (
                profile_complete is True
                and has_bank_accounts is True
                and global_state.get("has_imported_transactions") is not True
                and (
                    mode == "free_chat"
                    or (mode == "onboarding" and onboarding_step in {"categories", "budget"})
                )
            )
            if should_re_gate_import:
                updated_global_state = _build_onboarding_global_state(
                    global_state,
                    onboarding_step="import",
                    onboarding_substep="import_select_account",
                )
                updated_global_state["has_imported_transactions"] = False
                state_dict["global_state"] = _normalize_onboarding_step_substep(updated_global_state)
                state_dict.pop("import_context", None)
                updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                updated_chat_state["state"] = state_dict
                profiles_repository.update_chat_state(
                    profile_id=profile_id,
                    user_id=auth_user_id,
                    chat_state=updated_chat_state,
                )
                return ChatResponse(
                    reply="Avant de continuer, tu dois importer un relevé. Quel compte veux-tu importer ?",
                    tool_result=None,
                    plan=None,
                )

            if mode == "onboarding" and onboarding_step == "bank_accounts" and hasattr(profiles_repository, "list_bank_accounts") and hasattr(profiles_repository, "ensure_bank_accounts"):
                substep = global_state.get("onboarding_substep") or "bank_accounts_collect"
                existing_accounts = profiles_repository.list_bank_accounts(profile_id=profile_id)

                if substep == "bank_accounts_collect":
                    if _is_no(payload.message):
                        if existing_accounts:
                            updated_global_state = _build_onboarding_global_state(
                                {
                                    **global_state,
                                    "bank_accounts_confirmed": True,
                                    "has_bank_accounts": True,
                                },
                                onboarding_step="import",
                                onboarding_substep="import_select_account",
                            )
                            updated_global_state["bank_accounts_confirmed"] = True
                            updated_global_state["has_bank_accounts"] = True
                            state_dict["global_state"] = updated_global_state
                            updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                            updated_chat_state["state"] = state_dict
                            profiles_repository.update_chat_state(
                                profile_id=profile_id,
                                user_id=auth_user_id,
                                chat_state=updated_chat_state,
                            )
                            return ChatResponse(
                                reply="Parfait. Quel compte veux-tu importer ?",
                                tool_result=None,
                                plan=None,
                            )
                        return ChatResponse(
                            reply="Il faut au moins une banque pour continuer l’onboarding.",
                            tool_result=None,
                            plan=None,
                        )

                    if existing_accounts and not bool(global_state.get("bank_accounts_confirmed", False)):
                        accounts_display = _format_accounts_for_reply(existing_accounts)
                        updated_global_state = _build_bank_accounts_onboarding_global_state(
                            global_state,
                            onboarding_substep="bank_accounts_confirm",
                        )
                        updated_global_state["has_bank_accounts"] = True
                        state_dict["global_state"] = updated_global_state
                        updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                        updated_chat_state["state"] = state_dict
                        profiles_repository.update_chat_state(
                            profile_id=profile_id,
                            user_id=auth_user_id,
                            chat_state=updated_chat_state,
                        )
                        return ChatResponse(
                            reply=f"Tu as déjà ces comptes : {accounts_display}. Tu veux en ajouter un autre ou on passe à l’import ? (réponds « autre » ou « import »)",
                            tool_result=None,
                            plan=None,
                        )

                    matched_banks, unknown_segments = extract_canonical_banks(payload.message)
                    if not matched_banks:
                        normalized_message = payload.message.lower()
                        message_looks_like_request = any(hint in normalized_message for hint in _BANK_ACCOUNTS_REQUEST_HINTS)
                        if message_looks_like_request:
                            return ChatResponse(
                                reply="Avant de continuer, indique-moi tes banques (ex: UBS, Revolut).",
                                tool_result=None,
                                plan=None,
                            )
                        if unknown_segments:
                            return ChatResponse(
                                reply=(
                                    f"Je n’ai pas reconnu: {', '.join(unknown_segments)}. "
                                    "Peux-tu donner le nom exact de ta banque ?"
                                ),
                                tool_result=None,
                                plan=None,
                            )
                        return ChatResponse(
                            reply="Indique-moi tes banques/comptes (ex: 'UBS, Revolut').",
                            tool_result=None,
                            plan=None,
                        )

                    profiles_repository.ensure_bank_accounts(profile_id=profile_id, names=matched_banks)
                    refreshed_accounts = profiles_repository.list_bank_accounts(profile_id=profile_id)
                    accounts_display = _format_accounts_for_reply(refreshed_accounts)
                    updated_global_state = _build_bank_accounts_onboarding_global_state(
                        global_state,
                        onboarding_substep="bank_accounts_confirm",
                    )
                    updated_global_state["has_bank_accounts"] = True
                    state_dict["global_state"] = updated_global_state
                    updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                    updated_chat_state["state"] = state_dict
                    profiles_repository.update_chat_state(
                        profile_id=profile_id,
                        user_id=auth_user_id,
                        chat_state=updated_chat_state,
                    )
                    action_prompt = "Tu veux en ajouter un autre ou on passe à l’import ? (réponds « autre » ou « import »)"
                    if len(matched_banks) == 1:
                        reply = f"OK, compte {matched_banks[0]} ajouté. {action_prompt}"
                    else:
                        reply = f"OK, comptes {', '.join(matched_banks)} ajoutés. {action_prompt}"
                    return ChatResponse(reply=reply, tool_result=None, plan=None)

                if substep == "bank_accounts_confirm":
                    normalized_message = _normalize_text(payload.message)
                    wants_other = _is_yes(payload.message) or "autre" in normalized_message
                    wants_import = _is_no(payload.message) or "import" in normalized_message

                    if wants_other:
                        updated_global_state = _build_bank_accounts_onboarding_global_state(
                            global_state,
                            onboarding_substep="bank_accounts_collect",
                        )
                        state_dict["global_state"] = updated_global_state
                        updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                        updated_chat_state["state"] = state_dict
                        profiles_repository.update_chat_state(
                            profile_id=profile_id,
                            user_id=auth_user_id,
                            chat_state=updated_chat_state,
                        )
                        return ChatResponse(
                            reply="Super. Donne-moi la banque à ajouter (ex: UBS).",
                            tool_result=None,
                            plan=None,
                        )
                    if wants_import:
                        if not existing_accounts:
                            return ChatResponse(
                                reply="Il faut au moins une banque pour continuer l’onboarding.",
                                tool_result=None,
                                plan=None,
                            )

                        updated_global_state = _build_onboarding_global_state(
                            {
                                **global_state,
                                "bank_accounts_confirmed": True,
                                "has_bank_accounts": bool(existing_accounts),
                            },
                            onboarding_step="import",
                            onboarding_substep="import_select_account",
                        )
                        updated_global_state["bank_accounts_confirmed"] = True
                        updated_global_state["has_bank_accounts"] = bool(existing_accounts)
                        state_dict["global_state"] = updated_global_state
                        updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                        updated_chat_state["state"] = state_dict

                        if len(existing_accounts) == 1:
                            selected_account = existing_accounts[0]
                            state_dict["import_context"] = {
                                "selected_bank_account_id": str(selected_account.get("id")),
                                "selected_bank_account_name": str(selected_account.get("name", "")),
                            }
                            updated_chat_state["state"] = state_dict
                            profiles_repository.update_chat_state(
                                profile_id=profile_id,
                                user_id=auth_user_id,
                                chat_state=updated_chat_state,
                            )
                            account_name = str(selected_account.get("name", "ce compte")).strip() or "ce compte"
                            return ChatResponse(
                                reply=f"Parfait. Envoie ton fichier de relevé {account_name} (CSV).",
                                tool_result=_build_import_file_ui_request(state_dict.get("import_context")),
                                plan=None,
                            )

                        profiles_repository.update_chat_state(
                            profile_id=profile_id,
                            user_id=auth_user_id,
                            chat_state=updated_chat_state,
                        )
                        account_names = " / ".join(
                            str(account.get("name", "")).strip()
                            for account in existing_accounts
                            if str(account.get("name", "")).strip()
                        )
                        return ChatResponse(
                            reply=f"Parfait. Quel compte veux-tu importer ? {account_names}",
                            tool_result=None,
                            plan=None,
                        )
                    return ChatResponse(reply="Réponds « autre » ou « import ».", tool_result=None, plan=None)

            if mode == "onboarding" and onboarding_step == "import" and global_state.get("onboarding_substep") == "import_select_account" and hasattr(profiles_repository, "list_bank_accounts"):
                existing_accounts = profiles_repository.list_bank_accounts(profile_id=profile_id)
                matched_account = _match_bank_account_name(payload.message, existing_accounts)
                if matched_account is None:
                    return ChatResponse(
                        reply=(
                            "Je ne trouve pas ce compte. Comptes dispo: "
                            f"{_format_accounts_for_reply(existing_accounts)}"
                        ),
                        tool_result=None,
                        plan=None,
                    )

                updated_state = dict(state_dict)
                updated_state["import_context"] = {
                    "selected_bank_account_id": str(matched_account.get("id")),
                    "selected_bank_account_name": str(matched_account.get("name", "")),
                }
                updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                updated_chat_state["state"] = updated_state
                profiles_repository.update_chat_state(
                    profile_id=profile_id,
                    user_id=auth_user_id,
                    chat_state=updated_chat_state,
                )
                ui_request = _build_import_file_ui_request(updated_state.get("import_context"))
                account_name = str(matched_account.get("name", "ce compte")).strip() or "ce compte"
                return ChatResponse(
                    reply=f"Parfait. Envoie ton fichier de relevé {account_name} (CSV).",
                    tool_result=ui_request,
                    plan=None,
                )

            if mode == "onboarding" and onboarding_step == "categories":
                substep = global_state.get("onboarding_substep")
                if substep in {"categories_intro", "categories_bootstrap"}:
                    ensure_result = profiles_repository.ensure_system_categories(
                        profile_id=profile_id,
                        categories=_build_system_categories_payload(),
                    )
                    created_count = int(ensure_result.get("created_count", 0))
                    system_total = int(ensure_result.get("system_total_count", 0))

                    merchants_without_category = profiles_repository.list_merchants_without_category(profile_id=profile_id)
                    classified_count, remaining_count, invalid_count = _classify_merchants_without_category(
                        profiles_repository=profiles_repository,
                        profile_id=profile_id,
                    )

                    updated_global_state = _build_onboarding_global_state(
                        global_state,
                        onboarding_step="categories",
                        onboarding_substep="categories_review",
                    )
                    updated_global_state = _normalize_onboarding_step_substep(updated_global_state)
                    state_dict["global_state"] = updated_global_state
                    updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                    updated_chat_state["state"] = state_dict
                    profiles_repository.update_chat_state(
                        profile_id=profile_id,
                        user_id=auth_user_id,
                        chat_state=updated_chat_state,
                    )
                    return ChatResponse(
                        reply=(
                            f"✅ Import terminé. Catégories créées : {created_count} (système total: {system_total}).\n"
                            "Je reconnais d’abord ce que je peux automatiquement, puis je complète avec l’IA pour le reste.\n"
                            f"C’est fait. Marchands classés : {classified_count}/{len(merchants_without_category)}. "
                            + (
                                "Tout est classé.\n"
                                if remaining_count == 0
                                else (
                                    f"Il reste {remaining_count} marchands.\n"
                                    "Tu préfères : (1) Je continue maintenant "
                                    f"({remaining_count} restants) (2) On s’arrête là et tu regardes le rapport. Réponds 1 ou 2."
                                )
                            )
                            + (
                                f"\n⚠️ {invalid_count} marchand(s) ont un identifiant invalide et ne peuvent pas être classés automatiquement."
                                if invalid_count > 0
                                else ""
                            )
                        ),
                        tool_result=None,
                        plan=None,
                    )

                if substep == "categories_review":
                    if payload.message.strip() == "1":
                        ensure_result = profiles_repository.ensure_system_categories(
                            profile_id=profile_id,
                            categories=_build_system_categories_payload(),
                        )
                        system_total = int(ensure_result.get("system_total_count", 0))
                        classified_count, remaining_count, invalid_count = _classify_merchants_without_category(
                            profiles_repository=profiles_repository,
                            profile_id=profile_id,
                        )

                        if remaining_count == 0:
                            updated_global_state = _build_free_chat_global_state(global_state)
                            updated_global_state = _normalize_onboarding_step_substep(updated_global_state)
                            state_dict["global_state"] = updated_global_state
                            updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                            updated_chat_state["state"] = state_dict
                            profiles_repository.update_chat_state(
                                profile_id=profile_id,
                                user_id=auth_user_id,
                                chat_state=updated_chat_state,
                            )
                            report_url = _build_spending_pdf_url()
                            done_reply = (
                                f"OK, j’ai continué : {classified_count} nouveaux marchands classés. "
                                "Tout est classé. Voici ton rapport : "
                                f"[Ouvrir le PDF]({report_url})."
                            )
                            if invalid_count > 0:
                                done_reply += (
                                    f" ⚠️ {invalid_count} marchand(s) ont un identifiant invalide et ne peuvent pas être classés automatiquement."
                                )

                            return ChatResponse(
                                reply=done_reply,
                                tool_result=_build_open_pdf_ui_request(report_url),
                                plan=None,
                            )

                        review_reply = (
                            f"OK, j’ai continué : {classified_count} nouveaux marchands classés. "
                            f"Il reste {remaining_count}. Tu préfères : (1) Je continue maintenant "
                            f"({remaining_count} restants) (2) On s’arrête là et tu regardes le rapport. "
                            f"Catégories système disponibles : {system_total}."
                        )
                        if invalid_count > 0:
                            review_reply += (
                                f" ⚠️ {invalid_count} marchand(s) ont un identifiant invalide et ne peuvent pas être classés automatiquement."
                            )

                        return ChatResponse(
                            reply=review_reply,
                            tool_result=None,
                            plan=None,
                        )
                    if payload.message.strip() == "2":
                        report_url = _build_spending_pdf_url()
                        updated_global_state = _build_free_chat_global_state(global_state)
                        updated_global_state = _normalize_onboarding_step_substep(updated_global_state)
                        state_dict["global_state"] = updated_global_state
                        updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                        updated_chat_state["state"] = state_dict
                        profiles_repository.update_chat_state(
                            profile_id=profile_id,
                            user_id=auth_user_id,
                            chat_state=updated_chat_state,
                        )
                        return ChatResponse(
                            reply=(
                                "Super, on peut s’arrêter ici pour l’instant. "
                                f"Voici ton rapport : [Ouvrir le PDF]({report_url}).\n"
                                "Si tu veux, je peux aussi te faire un résumé ici (top catégories + top marchands)."
                            ),
                            tool_result=_build_open_pdf_ui_request(report_url),
                            plan=None,
                        )
                    return ChatResponse(reply="Réponds 1 ou 2.", tool_result=None, plan=None)

            if mode == "onboarding" and onboarding_step == "report" and global_state.get("onboarding_substep") == "report_offer":
                if _is_yes(payload.message):
                    month_value: str | None = None
                    start_date_value: str | None = None
                    end_date_value: str | None = None

                    last_query = state_dict.get("last_query") if isinstance(state_dict, dict) else None
                    if isinstance(last_query, dict):
                        if isinstance(last_query.get("month"), str) and str(last_query.get("month")).strip():
                            month_value = str(last_query.get("month")).strip()
                        if month_value is None:
                            filters = last_query.get("filters") if isinstance(last_query.get("filters"), dict) else None
                            date_range = filters.get("date_range") if isinstance(filters, dict) else None
                            if isinstance(date_range, dict):
                                start_raw = date_range.get("start_date")
                                end_raw = date_range.get("end_date")
                                if isinstance(start_raw, str) and isinstance(end_raw, str):
                                    start_date_value = start_raw
                                    end_date_value = end_raw

                    if month_value is None and (start_date_value is None or end_date_value is None):
                        resolved_start, resolved_end = _resolve_report_date_range(
                            month=None,
                            start_date=None,
                            end_date=None,
                            state_dict=state_dict,
                            profile_id=profile_id,
                        )
                        start_date_value = resolved_start.isoformat()
                        end_date_value = resolved_end.isoformat()

                    report_url = _build_spending_pdf_url(
                        month=month_value,
                        start_date=start_date_value,
                        end_date=end_date_value,
                    )
                    updated_global_state = _build_free_chat_global_state(global_state)
                    updated_global_state = _normalize_onboarding_step_substep(updated_global_state)
                    state_dict["global_state"] = updated_global_state
                    updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                    updated_chat_state["state"] = state_dict
                    profiles_repository.update_chat_state(
                        profile_id=profile_id,
                        user_id=auth_user_id,
                        chat_state=updated_chat_state,
                    )
                    return ChatResponse(
                        reply=(
                            f"Voici ton rapport PDF : [Ouvrir le PDF]({report_url}). "
                            "Dis-moi si tu veux un autre mois/période."
                        ),
                        tool_result=_build_open_pdf_ui_request(report_url),
                        plan=None,
                    )
                if _is_no(payload.message):
                    updated_global_state = _build_free_chat_global_state(global_state)
                    updated_global_state = _normalize_onboarding_step_substep(updated_global_state)
                    state_dict["global_state"] = updated_global_state
                    updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                    updated_chat_state["state"] = state_dict
                    profiles_repository.update_chat_state(
                        profile_id=profile_id,
                        user_id=auth_user_id,
                        chat_state=updated_chat_state,
                    )
                    return ChatResponse(
                        reply=(
                            "OK — on reste en chat libre. Tu peux me demander un rapport PDF quand tu veux "
                            "(ex: 'rapport pdf janvier 2026')."
                        ),
                        tool_result=None,
                        plan=None,
                    )
                return ChatResponse(reply="Réponds OUI ou NON.", tool_result=None, plan=None)

        pending_clarification = state_dict.get("pending_clarification") if isinstance(state_dict, dict) else None
        if (
            isinstance(pending_clarification, dict)
            and pending_clarification.get("type") == "clarification_pending"
            and (
                isinstance(pending_clarification.get("tool_name"), str)
                or pending_clarification.get("mode") == "date_range_only"
            )
        ):
            resolved_pending = _resolve_pending_clarification_payload(
                message=payload.message,
                pending_clarification=pending_clarification,
                state_dict=state_dict if isinstance(state_dict, dict) else None,
            )
            if resolved_pending is not None:
                tool_name, tool_payload, remaining_missing_fields = resolved_pending
                if not remaining_missing_fields:
                    pending_result = get_tool_router().call(
                        tool_name,
                        tool_payload,
                        profile_id=profile_id,
                    )
                    state_after_pending = dict(state_dict) if isinstance(state_dict, dict) else {}
                    state_after_pending.pop("pending_clarification", None)
                    updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                    if state_after_pending:
                        updated_chat_state["state"] = state_after_pending
                    else:
                        updated_chat_state.pop("state", None)
                    profiles_repository.update_chat_state(
                        profile_id=profile_id,
                        user_id=auth_user_id,
                        chat_state=updated_chat_state,
                    )
                    pending_reply, serialized_pending_result = _build_pending_resolution_reply(
                        tool_name=tool_name,
                        tool_payload=tool_payload,
                        pending_result=pending_result,
                    )
                    return ChatResponse(
                        reply=pending_reply,
                        tool_result=serialized_pending_result,
                        plan=jsonable_encoder({"tool_name": tool_name, "payload": tool_payload}),
                    )

        if mode == "free_chat" and _is_pdf_report_request(payload.message):
            month_value, start_date_value, end_date_value = _resolve_report_period_from_message(
                message=payload.message,
                state_dict=state_dict if isinstance(state_dict, dict) else None,
                profile_id=profile_id,
            )
            report_url = _build_spending_pdf_url(
                month=month_value,
                start_date=start_date_value,
                end_date=end_date_value,
            )
            period_label = month_value or (
                f"du {start_date_value} au {end_date_value}"
                if start_date_value and end_date_value
                else "la période demandée"
            )
            plan_payload: dict[str, Any] = {}
            if month_value:
                plan_payload["month"] = month_value
            elif start_date_value and end_date_value:
                plan_payload["start_date"] = start_date_value
                plan_payload["end_date"] = end_date_value
            return ChatResponse(
                reply=f"Voici ton rapport PDF pour {period_label} : [Ouvrir le PDF]({report_url})",
                tool_result=_build_open_pdf_ui_request(report_url),
                plan={"tool_name": "finance_report_spending_pdf", "payload": plan_payload},
            )

        memory_for_loop = state_dict if isinstance(state_dict, dict) else None

        logger.info(
            "agent_chat_state_loaded active_task_present=%s memory_present=%s memory_keys=%s",
            isinstance(active_task, dict),
            isinstance(memory_for_loop, dict),
            sorted(memory_for_loop.keys()) if isinstance(memory_for_loop, dict) else [],
        )

        debug_enabled = isinstance(x_debug, str) and x_debug.strip() == "1"
        loop = get_agent_loop()
        handler = loop.handle_user_message
        handler_kwargs: dict[str, Any] = {
            "profile_id": profile_id,
            "active_task": active_task if isinstance(active_task, dict) else None,
            "memory": memory_for_loop,
        }
        if _handler_accepts_debug_kwarg(handler):
            handler_kwargs["debug"] = debug_enabled
        if _handler_accepts_global_state_kwarg(handler):
            handler_kwargs["global_state"] = global_state

        agent_reply = handler(payload.message, **handler_kwargs)

        response_plan = dict(agent_reply.plan) if isinstance(agent_reply.plan, dict) else agent_reply.plan

        memory_update = getattr(agent_reply, "memory_update", None)
        pending_from_clarification = _build_pending_clarification_from_tool_result(agent_reply.tool_result)
        if pending_from_clarification is not None:
            memory_update_dict = dict(memory_update) if isinstance(memory_update, dict) else {}
            memory_update_dict["pending_clarification"] = pending_from_clarification
            memory_update = memory_update_dict
        should_update_chat_state = (
            agent_reply.should_update_active_task
            or isinstance(memory_update, dict)
            or should_persist_global_state
        )

        updated_chat_state: dict[str, Any] | None = None
        if should_update_chat_state:
            updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
            if agent_reply.should_update_active_task:
                if agent_reply.active_task is None:
                    updated_chat_state["active_task"] = None
                else:
                    updated_chat_state["active_task"] = jsonable_encoder(agent_reply.active_task)

            merged_state = dict(state_dict) if isinstance(state_dict, dict) else {}
            if isinstance(memory_update, dict):
                for key, value in jsonable_encoder(memory_update).items():
                    if value is None:
                        merged_state.pop(key, None)
                    else:
                        merged_state[key] = value
            if merged_state:
                updated_chat_state["state"] = merged_state
            else:
                updated_chat_state.pop("state", None)

            logger.info(
                "agent_chat_state_updating has_memory_update=%s memory_update_keys=%s",
                isinstance(memory_update, dict),
                sorted(memory_update.keys()) if isinstance(memory_update, dict) else [],
            )

            try:
                profiles_repository.update_chat_state(
                    profile_id=profile_id,
                    user_id=auth_user_id,
                    chat_state=updated_chat_state,
                )
            except Exception:
                logger.exception("chat_state_update_failed profile_id=%s", profile_id)
                if not isinstance(response_plan, dict):
                    response_plan = {"warnings": ["chat_state_update_failed"]}
                else:
                    warnings = response_plan.get("warnings")
                    if isinstance(warnings, list):
                        warnings.append("chat_state_update_failed")
                    else:
                        response_plan["warnings"] = ["chat_state_update_failed"]

        tool_name = response_plan.get("tool_name") if isinstance(response_plan, dict) else None
        logger.info("agent_chat_completed tool_name=%s", tool_name)
        safe_tool_result = jsonable_encoder(agent_reply.tool_result)
        safe_plan = jsonable_encoder(response_plan)

        reply_text = agent_reply.reply
        reminder_state = (
            _normalize_onboarding_step_substep(global_state)
            if _is_valid_global_state(global_state)
            else None
        )
        has_valid_memory_update_global_state = False
        if isinstance(memory_update, dict):
            memory_update_global_state = None
            state_part = memory_update.get("state")
            if isinstance(state_part, dict):
                memory_update_global_state = state_part.get("global_state")
            if _is_valid_global_state(memory_update_global_state):
                reminder_state = _normalize_onboarding_step_substep(memory_update_global_state)
                has_valid_memory_update_global_state = True

        updated_chat_global_state = None
        if isinstance(updated_chat_state, dict):
            updated_chat_global_state = updated_chat_state.get("state", {}).get("global_state")
        if _is_valid_global_state(updated_chat_global_state) and not has_valid_memory_update_global_state:
            reminder_state = _normalize_onboarding_step_substep(updated_chat_global_state)

        reminder = _build_onboarding_reminder(reminder_state)
        if reminder:
            reply_text = f"{reply_text}\n\n{reminder}"

        return ChatResponse(reply=reply_text, tool_result=safe_tool_result, plan=safe_plan)
    except HTTPException as exc:
        if exc.status_code in {401, 403}:
            raise
        logger.exception("agent_chat_http_exception", exc_info=exc)
        return ChatResponse(
            reply="Une erreur est survenue côté serveur. Réessaie dans quelques secondes.",
            tool_result={"error": "internal_server_error"},
            plan=None,
        )

    except Exception:
        logger.exception(
            "agent_chat_unhandled_error",
            extra={"path": "/agent/chat", "profile_id": str(profile_id) if profile_id is not None else None},
        )
        return ChatResponse(
            reply="Une erreur est survenue côté serveur. Réessaie dans quelques secondes.",
            tool_result={"error": "internal_server_error"},
            plan=None,
        )


@app.post("/agent/reset-session")
def reset_session(authorization: str | None = Header(default=None)) -> dict[str, bool]:
    """Reset persisted chat session state for the authenticated profile."""

    auth_user_id, profile_id = _resolve_authenticated_profile(authorization)
    profiles_repository = get_profiles_repository()

    chat_state = profiles_repository.get_chat_state(profile_id=profile_id, user_id=auth_user_id)
    updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
    updated_chat_state["active_task"] = None

    existing_state = updated_chat_state.get("state")
    if isinstance(existing_state, dict):
        preserved_state = dict(existing_state)
        preserved_state.pop("pending_clarification", None)
        if preserved_state:
            updated_chat_state["state"] = preserved_state
        else:
            updated_chat_state.pop("state", None)

    profiles_repository.update_chat_state(
        profile_id=profile_id,
        user_id=auth_user_id,
        chat_state=updated_chat_state,
    )
    return {"ok": True}


@app.post("/debug/hard-reset")
def debug_hard_reset(
    payload: HardResetPayload,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Hard reset current authenticated profile data (debug only)."""

    if os.getenv("DEBUG_ENDPOINTS_ENABLED") != "true":
        raise HTTPException(status_code=404, detail="Not found")

    if payload.confirm is not True:
        raise HTTPException(status_code=400, detail="confirm=true is required")

    auth_user_id, profile_id = _resolve_authenticated_profile(authorization)
    repo = get_profiles_repository()
    repo.hard_reset_profile(profile_id=profile_id, user_id=auth_user_id)
    return {"ok": True}


@app.get("/finance/bank-accounts")
def list_bank_accounts(authorization: str | None = Header(default=None)) -> Any:
    """Return bank accounts for the authenticated profile."""

    _, profile_id = _resolve_authenticated_profile(authorization)
    result = get_tool_router().call("finance_bank_accounts_list", {}, profile_id=profile_id)
    if isinstance(result, ToolError):
        raise HTTPException(status_code=400, detail=result.message)
    return jsonable_encoder(result)


def _parse_iso_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name} format. Expected YYYY-MM-DD") from exc


def _resolve_report_date_range(
    *,
    month: str | None,
    start_date: str | None,
    end_date: str | None,
    state_dict: dict[str, Any] | None,
    profile_id: UUID,
) -> tuple[date, date]:
    def _extract_start_end_from_date_range(date_range: Any) -> tuple[str, str] | None:
        if not isinstance(date_range, dict):
            return None

        start = date_range.get("start_date")
        end = date_range.get("end_date")
        if isinstance(start, str) and isinstance(end, str):
            return start, end

        # Legacy compatibility.
        legacy_start = date_range.get("start")
        legacy_end = date_range.get("end")
        if isinstance(legacy_start, str) and isinstance(legacy_end, str):
            return legacy_start, legacy_end

        return None

    if month:
        try:
            month_start = datetime.strptime(month, "%Y-%m").date().replace(day=1)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid month format. Expected YYYY-MM") from exc
        _, last_day = calendar.monthrange(month_start.year, month_start.month)
        return month_start, month_start.replace(day=last_day)

    if start_date or end_date:
        if not start_date or not end_date:
            raise HTTPException(status_code=400, detail="start_date and end_date must be provided together")
        parsed_start = _parse_iso_date(start_date, "start_date")
        parsed_end = _parse_iso_date(end_date, "end_date")
        if parsed_start > parsed_end:
            raise HTTPException(status_code=400, detail="start_date must be before or equal to end_date")
        return parsed_start, parsed_end

    last_query = state_dict.get("last_query") if isinstance(state_dict, dict) else None
    if isinstance(last_query, dict):
        memory_month = last_query.get("month")
        if isinstance(memory_month, str) and memory_month.strip():
            return _resolve_report_date_range(
                month=memory_month,
                start_date=None,
                end_date=None,
                state_dict=None,
                profile_id=profile_id,
            )
        memory_date_range = last_query.get("date_range")
        extracted_memory_range = _extract_start_end_from_date_range(memory_date_range)
        if extracted_memory_range is None:
            filters = last_query.get("filters")
            extracted_memory_range = _extract_start_end_from_date_range(
                filters.get("date_range") if isinstance(filters, dict) else None
            )

        if extracted_memory_range is not None:
            memory_start, memory_end = extracted_memory_range
            return _resolve_report_date_range(
                month=None,
                start_date=memory_start,
                end_date=memory_end,
                state_dict=None,
                profile_id=profile_id,
            )

    aggregate_result = get_tool_router().call(
        "finance_releves_aggregate",
        {
            "group_by": "month",
            "direction": RelevesDirection.DEBIT_ONLY.value,
        },
        profile_id=profile_id,
    )
    if not isinstance(aggregate_result, ToolError):
        aggregate_payload = jsonable_encoder(aggregate_result)
        groups = aggregate_payload.get("groups") if isinstance(aggregate_payload, dict) else None
        if isinstance(groups, dict) and groups:
            latest_month = max((key for key in groups if isinstance(key, str)), default=None)
            if latest_month:
                return _resolve_report_date_range(
                    month=latest_month,
                    start_date=None,
                    end_date=None,
                    state_dict=None,
                    profile_id=profile_id,
                )

    today = date.today()
    _, last_day = calendar.monthrange(today.year, today.month)
    return today.replace(day=1), today.replace(day=last_day)


def _resolve_report_period_from_message(
    *,
    message: str,
    state_dict: dict[str, Any] | None,
    profile_id: UUID,
) -> tuple[str | None, str | None, str | None]:
    """Resolve report period from explicit message period, memory, then backend fallback."""

    normalized_message = _normalize_text(message)
    month_with_year_match = re.search(r"\b([a-z]+)\s+(\d{4})\b", normalized_message)
    if month_with_year_match is not None:
        month_name = month_with_year_match.group(1)
        year_value = int(month_with_year_match.group(2))
        month_number = _FRENCH_MONTH_TO_NUMBER.get(month_name)
        if month_number is not None:
            return f"{year_value:04d}-{month_number:02d}", None, None

    if "ce mois" in normalized_message:
        today = date.today()
        return f"{today.year:04d}-{today.month:02d}", None, None

    period_payload = period_payload_from_message(message)
    date_range = period_payload.get("date_range") if isinstance(period_payload, dict) else None
    if isinstance(date_range, dict):
        start_date = date_range.get("start_date")
        end_date = date_range.get("end_date")
        if isinstance(start_date, str) and isinstance(end_date, str):
            return None, start_date, end_date

    explicit_month = period_payload.get("month") if isinstance(period_payload, dict) else None
    if isinstance(explicit_month, str) and explicit_month.strip():
        return explicit_month.strip(), None, None

    resolved_start, resolved_end = _resolve_report_date_range(
        month=None,
        start_date=None,
        end_date=None,
        state_dict=state_dict,
        profile_id=profile_id,
    )
    return None, resolved_start.isoformat(), resolved_end.isoformat()


def _is_pdf_report_request(message: str) -> bool:
    """Return True when message clearly asks for a spending report PDF."""

    normalized = _normalize_text(message)
    has_report_word = "rapport" in normalized or "report" in normalized or "bilan" in normalized
    has_pdf_word = "pdf" in normalized
    return has_report_word and has_pdf_word


def _pick_first_non_empty_string(values: list[object]) -> str | None:
    """Return first non-empty string candidate."""

    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _fetch_spending_transactions(
    *,
    profile_id: UUID,
    payload: dict[str, Any],
) -> tuple[list[SpendingTransactionRow], bool, bool]:
    """Fetch DEBIT_ONLY transactions for spending PDF detail page."""

    router = get_tool_router()
    query_payload = {**payload, "limit": 500, "offset": 0}
    result = router.call("finance_releves_search", query_payload, profile_id=profile_id)

    if isinstance(result, ToolError) and result.code == ToolErrorCode.UNKNOWN_TOOL:
        result = router.call("finance_releves_list", query_payload, profile_id=profile_id)

    if isinstance(result, ToolError):
        logger.warning(
            "finance_spending_report_transactions_unavailable",
            extra={
                "profile_id": str(profile_id),
                "error_code": result.code.value,
                "error_message": result.message,
            },
        )
        return [], False, True

    payload_dict = jsonable_encoder(result)
    items = payload_dict.get("items") if isinstance(payload_dict, dict) else None
    if not isinstance(items, list):
        return [], False, False

    rows: list[SpendingTransactionRow] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        raw_amount = item.get("montant")
        try:
            amount = abs(Decimal(str(raw_amount)))
        except (InvalidOperation, TypeError, ValueError):
            continue

        date_value = item.get("date")
        date_label = str(date_value) if date_value is not None else ""

        merchant = _pick_first_non_empty_string(
            [
                item.get("merchant"),
                item.get("merchant_name"),
                item.get("payee"),
                item.get("libelle"),
            ]
        ) or "Inconnu"
        category = _pick_first_non_empty_string(
            [
                item.get("categorie"),
                item.get("category_name"),
            ]
        ) or "Sans catégorie"

        rows.append(
            SpendingTransactionRow(
                date=date_label,
                merchant=merchant,
                category=category,
                amount=amount,
            )
        )

    rows.sort(key=lambda row: row.date, reverse=True)

    total = payload_dict.get("total") if isinstance(payload_dict, dict) else None
    truncated = isinstance(total, int) and total > len(rows)
    return rows, truncated, False


@app.get("/finance/reports/spending.pdf")
def get_spending_report_pdf(
    authorization: str | None = Header(default=None),
    start_date: str | None = None,
    end_date: str | None = None,
    month: str | None = None,
) -> Response:
    auth_user_id, profile_id = _resolve_authenticated_profile(authorization)
    profiles_repository = get_profiles_repository()
    chat_state = profiles_repository.get_chat_state(profile_id=profile_id, user_id=auth_user_id)
    state_dict = chat_state.get("state") if isinstance(chat_state, dict) else None

    period_start, period_end = _resolve_report_date_range(
        month=month,
        start_date=start_date,
        end_date=end_date,
        state_dict=state_dict if isinstance(state_dict, dict) else None,
        profile_id=profile_id,
    )
    logger.info(
        "finance_spending_report_requested",
        extra={
            "profile_id": str(profile_id),
            "start_date": period_start.isoformat(),
            "end_date": period_end.isoformat(),
        },
    )

    payload = {
        "date_range": {
            "start_date": period_start.isoformat(),
            "end_date": period_end.isoformat(),
        },
        "direction": RelevesDirection.DEBIT_ONLY.value,
    }
    sum_result = get_tool_router().call("finance_releves_sum", payload, profile_id=profile_id)
    if isinstance(sum_result, ToolError):
        raise HTTPException(status_code=400, detail=sum_result.message)

    categories_result = get_tool_router().call(
        "finance_releves_aggregate",
        {
            **payload,
            "group_by": "categorie",
        },
        profile_id=profile_id,
    )
    if isinstance(categories_result, ToolError):
        raise HTTPException(status_code=400, detail=categories_result.message)

    sum_payload = jsonable_encoder(sum_result)
    aggregate_payload = jsonable_encoder(categories_result)
    raw_groups = aggregate_payload.get("groups") if isinstance(aggregate_payload, dict) else {}
    currency = str(sum_payload.get("currency") or aggregate_payload.get("currency") or "CHF")

    category_rows: list[SpendingCategoryRow] = []
    if isinstance(raw_groups, dict):
        for category_name, group in raw_groups.items():
            if not isinstance(group, dict):
                continue
            total_raw = group.get("total")
            try:
                amount = abs(Decimal(str(total_raw)))
            except Exception:
                continue
            if amount == Decimal("0"):
                continue
            name = category_name if isinstance(category_name, str) and category_name.strip() else "Sans catégorie"
            category_rows.append(SpendingCategoryRow(name=name, amount=amount))

    transactions, transactions_truncated, transactions_unavailable = _fetch_spending_transactions(
        profile_id=profile_id,
        payload=payload,
    )

    total = abs(Decimal(str(sum_payload.get("total") or "0")))
    count = int(sum_payload.get("count") or 0)
    average = abs(Decimal(str(sum_payload.get("average") or "0")))
    period_label = f"{period_start.isoformat()} → {period_end.isoformat()}"
    filename_period = period_start.strftime("%Y-%m") if period_start.day == 1 else f"{period_start.isoformat()}_{period_end.isoformat()}"

    pdf_bytes = generate_spending_report_pdf(
        SpendingReportData(
            period_label=period_label,
            start_date=period_start.isoformat(),
            end_date=period_end.isoformat(),
            total=total,
            count=count,
            average=average,
            currency=currency,
            categories=category_rows,
            transactions=transactions,
            transactions_truncated=transactions_truncated,
            transactions_unavailable=transactions_unavailable,
        )
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="rapport-depenses-{filename_period}.pdf"'},
    )


@app.post("/finance/releves/import")
def import_releves(payload: ImportRequestPayload, authorization: str | None = Header(default=None)) -> Any:
    """Import bank statements using backend tool router."""

    auth_user_id, profile_id = _resolve_authenticated_profile(authorization)
    tool_payload: dict[str, Any] = {
        "files": [
            {"filename": import_file.filename, "content_base64": import_file.content_base64}
            for import_file in payload.files
        ],
        "import_mode": payload.import_mode,
        "modified_action": payload.modified_action,
    }
    if payload.bank_account_id:
        tool_payload["bank_account_id"] = payload.bank_account_id

    result = get_tool_router().call("finance_releves_import_files", tool_payload, profile_id=profile_id)
    if isinstance(result, ToolError):
        detail = result.message
        if result.details:
            detail = f"{result.message} ({result.details})"
        raise HTTPException(status_code=400, detail=detail)

    response_payload: dict[str, Any]
    if isinstance(result, dict):
        response_payload = dict(result)
    else:
        response_payload = jsonable_encoder(result)

    response_payload["ok"] = True
    imported_count = int(response_payload.get("imported_count") or 0)
    response_payload["transactions_imported"] = imported_count
    response_payload["transactions_imported_count"] = imported_count
    response_payload["date_range"] = _extract_import_date_range(response_payload)
    response_payload["bank_account_id"] = payload.bank_account_id

    bank_account_name = response_payload.get("bank_account_name")
    if not isinstance(bank_account_name, str) or not bank_account_name.strip():
        bank_account_name = None
        if payload.bank_account_id:
            bank_accounts_result = get_tool_router().call("finance_bank_accounts_list", {}, profile_id=profile_id)
            if not isinstance(bank_accounts_result, ToolError):
                encoded_accounts_result = jsonable_encoder(bank_accounts_result)
                if isinstance(encoded_accounts_result, dict):
                    account_items = encoded_accounts_result.get("items")
                    if isinstance(account_items, list):
                        for account in account_items:
                            if not isinstance(account, dict):
                                continue
                            if str(account.get("id")) == str(payload.bank_account_id):
                                candidate_name = account.get("name")
                                if isinstance(candidate_name, str) and candidate_name.strip():
                                    bank_account_name = candidate_name
                                break
    response_payload["bank_account_name"] = bank_account_name

    try:
        profiles_repository = get_profiles_repository()
        chat_state = profiles_repository.get_chat_state(profile_id=profile_id, user_id=auth_user_id)
        state = chat_state.get("state") if isinstance(chat_state, dict) else None
        state_dict = dict(state) if isinstance(state, dict) else {}
        global_state = state_dict.get("global_state") if isinstance(state_dict.get("global_state"), dict) else None

        if _is_valid_global_state(global_state):
            updated_global_state = _build_onboarding_global_state(
                global_state,
                onboarding_step="categories",
                onboarding_substep="categories_bootstrap",
            )
            updated_global_state["has_imported_transactions"] = True
            updated_global_state = _normalize_onboarding_step_substep(updated_global_state)
        else:
            updated_global_state = _build_onboarding_global_state(
                None,
                onboarding_step="categories",
                onboarding_substep="categories_bootstrap",
            )
            updated_global_state["has_imported_transactions"] = True

        state_dict["global_state"] = updated_global_state
        state_dict.pop("import_context", None)

        updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
        updated_chat_state["state"] = state_dict
        profiles_repository.update_chat_state(
            profile_id=profile_id,
            user_id=auth_user_id,
            chat_state=updated_chat_state,
        )
    except Exception:
        logger.exception("import_releves_chat_state_update_failed profile_id=%s", profile_id)
        warnings = response_payload.get("warnings")
        if isinstance(warnings, list):
            warnings.append("chat_state_update_failed")
        else:
            response_payload["warnings"] = ["chat_state_update_failed"]

    profiles_repository = None
    try:
        profiles_repository = get_profiles_repository()
        if hasattr(profiles_repository, "ensure_system_categories"):
            profiles_repository.ensure_system_categories(
                profile_id=profile_id,
                categories=_build_system_categories_payload(),
            )
        merchant_link_summary = _bootstrap_merchants_from_imported_releves(
            profiles_repository=profiles_repository,
            profile_id=profile_id,
            limit=500,
        )
        response_payload["merchant_linked_count"] = merchant_link_summary["linked_count"]
        response_payload["merchant_skipped_count"] = merchant_link_summary["skipped_count"]
        response_payload["merchant_processed_count"] = merchant_link_summary["processed_count"]
        response_payload["merchant_suggestions_created_count"] = merchant_link_summary["suggestions_created_count"]
    except Exception:
        logger.exception("import_releves_merchant_linking_failed profile_id=%s", profile_id)
        warnings = response_payload.get("warnings")
        if isinstance(warnings, list):
            warnings.append("merchant_linking_failed")
        else:
            response_payload["warnings"] = ["merchant_linking_failed"]

    merchant_alias_auto_resolve_payload: dict[str, Any] = {
        "attempted": False,
        "skipped_reason": None,
        "stats": None,
        "pending_total_count": None,
    }
    response_payload["merchant_alias_auto_resolve"] = merchant_alias_auto_resolve_payload

    if not _config.auto_resolve_merchant_aliases_enabled():
        merchant_alias_auto_resolve_payload["skipped_reason"] = "merchant_alias_auto_resolve_disabled"
    elif not _config.llm_enabled():
        merchant_alias_auto_resolve_payload["skipped_reason"] = "merchant_alias_auto_resolve_llm_disabled"
    else:
        try:
            if profiles_repository is None:
                profiles_repository = get_profiles_repository()
            if not hasattr(profiles_repository, "list_map_alias_suggestions"):
                merchant_alias_auto_resolve_payload["skipped_reason"] = "merchant_alias_auto_resolve_unsupported"
            else:
                auto_resolve_limit = _config.auto_resolve_merchant_aliases_limit()
                pending_map_alias_suggestions = profiles_repository.list_map_alias_suggestions(
                    profile_id=profile_id,
                    limit=auto_resolve_limit + 1,
                )
                pending_total_count = len(pending_map_alias_suggestions)
                if hasattr(profiles_repository, "count_map_alias_suggestions"):
                    counted_pending_total = profiles_repository.count_map_alias_suggestions(profile_id=profile_id)
                    if isinstance(counted_pending_total, int):
                        pending_total_count = counted_pending_total
                merchant_alias_auto_resolve_payload["pending_total_count"] = pending_total_count

                if not pending_map_alias_suggestions:
                    merchant_alias_auto_resolve_payload["skipped_reason"] = "merchant_alias_auto_resolve_no_suggestions"
                elif len(pending_map_alias_suggestions) > auto_resolve_limit:
                    merchant_alias_auto_resolve_payload["attempted"] = True
                    merchant_alias_auto_resolve_payload["skipped_reason"] = "merchant_alias_auto_resolve_partial"
                    merchant_alias_auto_resolve_payload["stats"] = resolve_pending_map_alias(
                        profile_id=profile_id,
                        profiles_repository=profiles_repository,
                        limit=auto_resolve_limit,
                    )
                    warnings = response_payload.get("warnings")
                    if isinstance(warnings, list):
                        warnings.append("merchant_alias_auto_resolve_partial")
                    else:
                        response_payload["warnings"] = ["merchant_alias_auto_resolve_partial"]
                else:
                    merchant_alias_auto_resolve_payload["attempted"] = True
                    merchant_alias_auto_resolve_payload["stats"] = resolve_pending_map_alias(
                        profile_id=profile_id,
                        profiles_repository=profiles_repository,
                        limit=auto_resolve_limit,
                    )
        except Exception:
            logger.exception("import_releves_merchant_alias_auto_resolve_failed profile_id=%s", profile_id)
            warnings = response_payload.get("warnings")
            if isinstance(warnings, list):
                warnings.append("merchant_alias_auto_resolve_failed")
            else:
                response_payload["warnings"] = ["merchant_alias_auto_resolve_failed"]
            merchant_alias_auto_resolve_payload["attempted"] = True
            merchant_alias_auto_resolve_payload["skipped_reason"] = "merchant_alias_auto_resolve_failed"
            merchant_alias_auto_resolve_payload["stats"] = None

    response_payload["merchant_suggestions_pending_count"] = 0
    response_payload["merchant_suggestions_applied_count"] = 0
    response_payload["merchant_suggestions_failed_count"] = 0

    if _config.llm_enabled():
        try:
            profiles_repository = get_profiles_repository()
            merchants = profiles_repository.list_merchants(profile_id=profile_id, limit=5000)
            merchants_by_id = {
                UUID(str(row.get("id"))): row
                for row in merchants
                if row.get("id")
            }
            suggestions, llm_run_id, usage, cleanup_stats = run_merchant_cleanup(
                profile_id=profile_id,
                profiles_repository=profiles_repository,
                merchants=merchants,
            )
            response_payload["merchant_cleanup_llm_run_id"] = llm_run_id
            response_payload["merchant_cleanup_usage"] = usage
            response_payload["merchant_cleanup_stats"] = cleanup_stats

            if int(cleanup_stats.get("parsed_count") or 0) == 0:
                warnings = response_payload.get("warnings")
                if isinstance(warnings, list):
                    warnings.append("merchant_cleanup_no_suggestions")
                else:
                    response_payload["warnings"] = ["merchant_cleanup_no_suggestions"]
                logger.info(
                    "import_releves_merchant_cleanup_no_suggestions profile_id=%s llm_run_id=%s stats=%s no_suggestions=%s",
                    profile_id,
                    llm_run_id,
                    cleanup_stats,
                    True,
                )

            suggestion_rows: list[dict[str, Any]] = []
            for suggestion in suggestions:
                auto_applied, error_message = _maybe_auto_apply_suggestion(
                    profiles_repository=profiles_repository,
                    profile_id=profile_id,
                    suggestion=suggestion,
                    merchants_by_id=merchants_by_id,
                )
                if error_message:
                    response_payload["merchant_suggestions_failed_count"] += 1
                    suggestion_rows.append({
                        **_build_suggestion_row(suggestion, status="failed"),
                        "llm_run_id": llm_run_id,
                        "error": error_message,
                    })
                    continue
                if auto_applied:
                    response_payload["merchant_suggestions_applied_count"] += 1
                    suggestion_rows.append({**_build_suggestion_row(suggestion, status="applied"), "llm_run_id": llm_run_id})
                else:
                    response_payload["merchant_suggestions_pending_count"] += 1
                    suggestion_rows.append({**_build_suggestion_row(suggestion, status="pending"), "llm_run_id": llm_run_id})

            profiles_repository.create_merchant_suggestions(
                profile_id=profile_id,
                suggestions=suggestion_rows,
            )
        except Exception:
            logger.exception("import_releves_merchant_cleanup_failed profile_id=%s", profile_id)
            warnings = response_payload.get("warnings")
            if isinstance(warnings, list):
                warnings.append("merchant_cleanup_failed")
            else:
                response_payload["warnings"] = ["merchant_cleanup_failed"]

    return jsonable_encoder(response_payload)


def _build_suggestion_row(
    suggestion: MerchantSuggestion,
    *,
    status: str,
) -> dict[str, Any]:
    suggested_name_norm = _normalize_text(suggestion.suggested_name or "") if suggestion.suggested_name else None
    return {
        "status": status,
        "action": suggestion.action,
        "source_merchant_id": str(suggestion.source_merchant_id) if suggestion.source_merchant_id else None,
        "target_merchant_id": str(suggestion.target_merchant_id) if suggestion.target_merchant_id else None,
        "suggested_name": suggestion.suggested_name,
        "suggested_name_norm": suggested_name_norm,
        "suggested_category": suggestion.suggested_category,
        "confidence": suggestion.confidence,
        "rationale": suggestion.rationale,
        "sample_aliases": suggestion.sample_aliases,
        "llm_model": _config.llm_model(),
    }


def _maybe_auto_apply_suggestion(
    *,
    profiles_repository: ProfilesRepository,
    profile_id: UUID,
    suggestion: MerchantSuggestion,
    merchants_by_id: dict[UUID, dict[str, Any]],
) -> tuple[bool, str | None]:
    try:
        if suggestion.action == "rename" and suggestion.source_merchant_id and suggestion.suggested_name and suggestion.confidence >= 0.90:
            profiles_repository.rename_merchant(
                profile_id=profile_id,
                merchant_id=suggestion.source_merchant_id,
                new_name=suggestion.suggested_name,
            )
            return True, None
        if suggestion.action == "merge" and suggestion.source_merchant_id and suggestion.target_merchant_id and suggestion.confidence >= 0.95:
            profiles_repository.merge_merchants(
                profile_id=profile_id,
                source_merchant_id=suggestion.source_merchant_id,
                target_merchant_id=suggestion.target_merchant_id,
            )
            return True, None
        if suggestion.action == "categorize" and suggestion.source_merchant_id and suggestion.suggested_category and suggestion.confidence >= 0.90:
            merchant = merchants_by_id.get(suggestion.source_merchant_id)
            if merchant is None:
                merchant = profiles_repository.get_merchant_by_id(
                    profile_id=profile_id,
                    merchant_id=suggestion.source_merchant_id,
                )
                if merchant is None:
                    return False, None
                merchants_by_id[suggestion.source_merchant_id] = merchant
            current_category = str(merchant.get("category") or "").strip()
            if current_category:
                return False, None
            profiles_repository.update_merchant_category(
                merchant_id=suggestion.source_merchant_id,
                category_name=suggestion.suggested_category,
            )
            return True, None
    except Exception as exc:
        return False, str(exc)

    return False, None


@app.post("/finance/merchants/suggestions/resolve")
def resolve_merchant_alias_suggestions(
    payload: MerchantAliasResolvePayload,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Resolve pending/failed map_alias suggestions for authenticated profile."""

    if not _config.llm_enabled():
        raise HTTPException(status_code=400, detail="LLM is disabled (set AGENT_LLM_ENABLED=1)")

    _, profile_id = _resolve_authenticated_profile(authorization)
    profiles_repository = get_profiles_repository()

    limit = max(1, min(int(payload.limit), 500))
    try:
        stats = resolve_pending_map_alias(
            profile_id=profile_id,
            profiles_repository=profiles_repository,
            limit=limit,
        )
    except Exception as exc:
        logger.exception("resolve_map_alias_suggestions_failed profile_id=%s", profile_id)
        raise HTTPException(status_code=500, detail="Failed to resolve map_alias suggestions") from exc

    return jsonable_encoder(stats)


@app.get("/finance/merchants/aliases/pending-count")
def get_pending_merchant_aliases_count(authorization: str | None = Header(default=None)) -> dict[str, int]:
    """Return count of pending/failed map_alias suggestions for authenticated profile."""

    _, profile_id = _resolve_authenticated_profile(authorization)
    profiles_repository = get_profiles_repository()

    pending_total_count: int | None = None
    if hasattr(profiles_repository, "count_map_alias_suggestions"):
        counted = profiles_repository.count_map_alias_suggestions(profile_id=profile_id)
        if isinstance(counted, int):
            pending_total_count = counted

    if pending_total_count is None:
        if hasattr(profiles_repository, "list_map_alias_suggestions"):
            suggestions = profiles_repository.list_map_alias_suggestions(profile_id=profile_id, limit=1000)
            pending_total_count = len(suggestions)
        else:
            logger.warning(
                "pending_alias_count_fallback_unavailable profile_id=%s repository=%s",
                profile_id,
                type(profiles_repository).__name__,
            )
            pending_total_count = 0

    return {"pending_total_count": max(0, pending_total_count)}


@app.post("/finance/merchants/aliases/resolve-pending")
def resolve_pending_merchant_aliases(
    payload: ResolvePendingMerchantAliasesPayload | None = None,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Resolve pending/failed map_alias suggestions in multiple batches for authenticated profile."""

    if not _config.llm_enabled():
        raise HTTPException(status_code=400, detail="LLM is disabled (set AGENT_LLM_ENABLED=1)")

    _, profile_id = _resolve_authenticated_profile(authorization)
    profiles_repository = get_profiles_repository()

    requested_limit = payload.limit if payload else None
    requested_max_batches = payload.max_batches if payload else None
    limit = max(1, min(int(requested_limit or _config.auto_resolve_merchant_aliases_limit()), 500))
    max_batches = max(1, min(int(requested_max_batches or 10), 100))

    pending_before: int | None = None
    if hasattr(profiles_repository, "count_map_alias_suggestions"):
        counted = profiles_repository.count_map_alias_suggestions(profile_id=profile_id)
        if isinstance(counted, int):
            pending_before = counted

    aggregated_stats: dict[str, Any] = {
        "processed": 0,
        "applied": 0,
        "failed": 0,
        "created_entities": 0,
        "linked_aliases": 0,
        "updated_transactions": 0,
        "warnings": [],
        "usage": {},
        "llm_run_id": None,
    }
    warning_values: set[str] = set()
    usage_totals: dict[str, int] = {}
    batches = 0
    pending_after: int | None = pending_before

    try:
        while batches < max_batches:
            if pending_after is not None and pending_after <= 0:
                break

            stats = resolve_pending_map_alias(
                profile_id=profile_id,
                profiles_repository=profiles_repository,
                limit=limit,
            )
            batches += 1

            if isinstance(stats, dict):
                for key in (
                    "processed",
                    "applied",
                    "failed",
                    "created_entities",
                    "linked_aliases",
                    "updated_transactions",
                ):
                    value = stats.get(key)
                    if isinstance(value, int):
                        aggregated_stats[key] += value

                usage = stats.get("usage")
                if isinstance(usage, dict):
                    for usage_key, usage_value in usage.items():
                        if isinstance(usage_value, int):
                            usage_totals[usage_key] = usage_totals.get(usage_key, 0) + usage_value

                warnings = stats.get("warnings")
                if isinstance(warnings, list):
                    for warning in warnings:
                        if isinstance(warning, str):
                            warning_values.add(warning)

                llm_run_id = stats.get("llm_run_id")
                if isinstance(llm_run_id, str) and llm_run_id.strip():
                    aggregated_stats["llm_run_id"] = llm_run_id

            if pending_before is None:
                pending_after = None
                break

            recounted_pending = profiles_repository.count_map_alias_suggestions(profile_id=profile_id)
            pending_after = recounted_pending if isinstance(recounted_pending, int) else None
            if pending_after is None:
                break

    except Exception as exc:
        logger.exception("resolve_pending_map_alias_batches_failed profile_id=%s", profile_id)
        raise HTTPException(status_code=500, detail="Failed to resolve pending merchant aliases") from exc

    aggregated_stats["usage"] = usage_totals
    aggregated_stats["warnings"] = sorted(warning_values)

    return {
        "ok": True,
        "type": "merchant_alias_resolve_result",
        "pending_before": pending_before,
        "pending_after": pending_after,
        "batches": batches,
        "stats": aggregated_stats,
    }


@app.post("/finance/merchants/suggestions/list")
def list_merchant_suggestions(
    payload: MerchantSuggestionsListPayload,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _, profile_id = _resolve_authenticated_profile(authorization)
    result = get_tool_router().call(
        "finance_merchants_suggest_fixes",
        payload.model_dump(),
        profile_id=profile_id,
    )
    if isinstance(result, ToolError):
        raise HTTPException(status_code=400, detail=result.message)
    return jsonable_encoder(result)


@app.post("/finance/merchants/suggestions/apply")
def apply_merchant_suggestion(
    payload: MerchantSuggestionApplyPayload,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _, profile_id = _resolve_authenticated_profile(authorization)
    result = get_tool_router().call(
        "finance_merchants_apply_suggestion",
        payload.model_dump(mode="json"),
        profile_id=profile_id,
    )
    if isinstance(result, ToolError):
        status = 404 if result.code.name == "NOT_FOUND" else 400
        raise HTTPException(status_code=status, detail=result.message)
    return jsonable_encoder(result)


@app.post("/finance/merchants/rename")
def rename_merchant(payload: RenameMerchantPayload, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Rename one merchant for the authenticated profile."""

    _, profile_id = _resolve_authenticated_profile(authorization)
    profiles_repository = get_profiles_repository()
    try:
        return profiles_repository.rename_merchant(
            profile_id=profile_id,
            merchant_id=payload.merchant_id,
            new_name=payload.name,
        )
    except ValueError as exc:
        error_message = str(exc)
        status_code = 404 if "not found" in error_message.lower() else 400
        raise HTTPException(status_code=status_code, detail=error_message) from exc


@app.post("/finance/merchants/merge")
def merge_merchants(payload: MergeMerchantsPayload, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Merge source merchant into target merchant for the authenticated profile."""

    _, profile_id = _resolve_authenticated_profile(authorization)
    profiles_repository = get_profiles_repository()
    try:
        return profiles_repository.merge_merchants(
            profile_id=profile_id,
            source_merchant_id=payload.source_merchant_id,
            target_merchant_id=payload.target_merchant_id,
        )
    except ValueError as exc:
        error_message = str(exc)
        status_code = 404 if "not found" in error_message.lower() else 400
        raise HTTPException(status_code=status_code, detail=error_message) from exc
