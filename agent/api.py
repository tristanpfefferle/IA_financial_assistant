"""FastAPI entrypoint for agent HTTP endpoints."""

from __future__ import annotations

import logging
import json
import inspect
import os
import re
import base64
import asyncio
import secrets
import unicodedata
import calendar
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import lru_cache
from typing import Any
from datetime import date, datetime
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse, Response, StreamingResponse
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
from agent.onboarding.profile_recap import build_profile_recap_reply
from agent.loops import build_default_registry
from agent.loops.registry import LoopRegistry
from agent.loops.router import parse_loop_context, route_message, serialize_loop_context
from agent.loops.types import LoopContext
from backend.factory import build_backend_tool_service
from backend.services.classification.decision_engine import normalize_merchant_alias
from backend.services.releves_import.bank_detector import detect_bank_from_csv_bytes
from backend.services.releves_import.classification import resolve_system_category_label
from backend.reporting import (
    SpendingCategoryRow,
    SpendingReportData,
    SpendingTransactionRow,
    generate_spending_report_pdf,
)
from backend.auth.supabase_auth import UnauthorizedError, extract_bearer_token, get_user_from_bearer_token
from backend.db.supabase_client import SupabaseClient, SupabaseRequestError, SupabaseSettings
from backend.repositories.profiles_repository import ProfilesRepository, SupabaseProfilesRepository
from backend.repositories.share_rules_repository import ShareRulesRepository, SupabaseShareRulesRepository
from backend.repositories.shared_expenses_repository import SharedExpensesRepository, SupabaseSharedExpensesRepository
from backend.repositories.import_jobs_repository import SupabaseImportJobsRepository
from backend.services.shared_expenses.effective_spending_adapter import compute_effective_spending_summary_safe
from backend.services.shared_expenses.suggestion_generator import generate_initial_shared_expense_suggestions
from shared.models import DateRange, RelevesDirection, RelevesImportMode, RelevesImportRequest, ToolError, ToolErrorCode


logger = logging.getLogger(__name__)


def _is_debug_request(request: Request, x_debug: str | None = None) -> bool:
    """Return whether debug error details should be included in responses."""

    if os.getenv("DEBUG_ENDPOINTS_ENABLED") == "true":
        return True

    debug_header = x_debug if isinstance(x_debug, str) else request.headers.get("x-debug")
    if isinstance(debug_header, str) and debug_header.lower() in {"1", "true"}:
        return True

    debug_param = request.query_params.get("debug")
    return isinstance(debug_param, str) and debug_param.lower() in {"1", "true"}


def _new_error_id() -> str:
    """Return a unique error identifier for correlating logs and API responses."""

    return str(uuid4())


def _new_short_error_id(prefix: str) -> str:
    """Return a compact prefixed error id (example: HHL-ABC123)."""

    token = base64.b32encode(secrets.token_bytes(4)).decode("ascii").rstrip("=")[:6]
    return f"{prefix}-{token}"


def _normalize_chat_state(value: Any) -> dict[str, Any]:
    """Return a safe chat state mapping."""

    if not isinstance(value, dict):
        return {}
    return value


_GLOBAL_STATE_MODES = {"onboarding", "guided_budget", "free_chat"}
_GLOBAL_STATE_ONBOARDING_STEPS = {"profile", "bank_accounts", "import", "categories", "budget", "report", None}
_GLOBAL_STATE_ONBOARDING_SUBSTEPS = {
    "profile_intro",
    "profile_collect",
    "profile_confirm",
    "profile_fix_select",
    "profile_fix_name",
    "profile_fix_birth_date",
    "bank_accounts_collect",
    "bank_accounts_confirm",
    "bank_accounts_fix_select",
    "import_select_account",
    "import_wait_ready",
    "categories_intro",
    "categories_bootstrap",
    "report_offer",
    "report_sent",
    None,
}
_PROFILE_FIX_SUBSTEPS = {"profile_fix_select", "profile_fix_name", "profile_fix_birth_date"}
_BANK_ACCOUNTS_FIX_SUBSTEP_PATTERN = re.compile(r"^bank_accounts_fix_")
_PROFILE_COMPLETION_FIELDS = ("first_name", "last_name", "birth_date")
_UI_FORM_SUBMIT_PREFIX = "__ui_form_submit__:"
_ONBOARDING_BANK_ACCOUNT_PRESET_OPTIONS: tuple[str, ...] = ("UBS", "Raiffeisen", "BCV", "Banque cantonale", "PostFinance", "Revolut", "Neon", "Yuh", "Zak", "Wise")
_BANK_ACCOUNTS_SELECTION_QUESTION = "Sélectionne les banques où tu as un compte. Tu peux en choisir plusieurs."
_BANK_ACCOUNTS_SELECTION_INTRO = (
    "Parfait 👍\n\n"
    "Maintenant, on va ajouter tes banques. Ça me permet d’associer correctement tes relevés et de classer tes transactions.\n\n"
    f"{_BANK_ACCOUNTS_SELECTION_QUESTION}"
)
_ONBOARDING_NAME_PATTERN = re.compile(
    r"^\s*([A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'\-]+)\s+([A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'\-]+)\s*$"
)
_ONBOARDING_NAME_PREFIX_PATTERN = re.compile(
    r"^\s*([A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'\-]+)\s+([A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'\-]+)\b"
)
_ONBOARDING_PROFILE_FIRST_NAME_HINT_PATTERN = re.compile(
    r"\b(?:je\s+m[’']?\s*app(?:el|ell?e|elle)?|moi\s+c[’']?est|c[’']?est|(?:pr[ée]nom|pernom|p[ée]rnom|prenon|pr[ée]non)\s*:?)\s+([^,.;!?\n]+)",
    flags=re.IGNORECASE,
)
_ONBOARDING_PROFILE_JE_MAPPELLE_TYPO_PATTERN = re.compile(
    r"\bje\s+m[’']?\s*app(?:el|ell?e|elle)?\b",
    flags=re.IGNORECASE,
)
_ONBOARDING_PROFILE_LAST_NAME_HINT_PATTERN = re.compile(
    r"\b(?:mon\s+nom(?:\s+de\s+famille)?|nom(?:\s+de\s+famille)?\s*:?)\s+([^,.;!?\n]+)",
    flags=re.IGNORECASE,
)
_ONBOARDING_PROFILE_TOKEN_PATTERN = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'\-]*")
_ONBOARDING_PROFILE_STOP_WORDS = {
    "je",
    "m",
    "me",
    "moi",
    "appelle",
    "appel",
    "app",
    "ap",
    "mappel",
    "mappelle",
    "est",
    "et",
    "c",
    "cest",
    "c'est",
    "ce",
    "mon",
    "ma",
    "prenom",
    "prénom",
    "pernom",
    "pérnom",
    "prenon",
    "prénon",
    "nom",
    "de",
    "famille",
    "suis",
    "mais",
    "bref",
}
_ONBOARDING_PROFILE_NON_NAME_HINTS = {
    "liste",
    "catégorie",
    "catégories",
    "transactions",
    "dépenses",
    "depenses",
    "revenus",
    "budget",
}
_ONBOARDING_PROFILE_TROLL_FIRST_NAMES = {
    "lol",
    "mdr",
    "lmao",
    "haha",
    "ptdr",
    "xd",
    "jai",
    "j'ai",
    "j",
    "pas",
    "prenom",
    "prénom",
    "pernom",
    "pérnom",
    "prenon",
    "prénon",
    "non",
    "aucun",
    "personne",
    "appel",
    "mappel",
    "mappelle",
}
_ONBOARDING_PROFILE_TROLL_LAST_NAMES = {"lol", "mdr", "lmao", "haha", "ptdr", "xd"}
_ONBOARDING_PROFILE_REFUSAL_PATTERNS = (
    re.compile(r"j[’']ai\s+pas\s+de\s+nom", flags=re.IGNORECASE),
    re.compile(r"je\s+n[’']ai\s+pas\s+de\s+nom", flags=re.IGNORECASE),
    re.compile(r"j[’']ai\s+pas\s+de\s+pr[ée]nom", flags=re.IGNORECASE),
    re.compile(r"je\s+n[’']ai\s+pas\s+de\s+pr[ée]nom", flags=re.IGNORECASE),
    re.compile(r"pas\s+de\s+pr[ée]nom", flags=re.IGNORECASE),
    re.compile(r"je\s+suis\s+pas\s+n[ée]", flags=re.IGNORECASE),
    re.compile(r"pas\s+n[ée]", flags=re.IGNORECASE),
    re.compile(r"pas\s+de\s+nom", flags=re.IGNORECASE),
    re.compile(r"aucun\s+nom", flags=re.IGNORECASE),
    re.compile(r"je\s+refuse", flags=re.IGNORECASE),
)
_ONBOARDING_PROFILE_TOXIC_PATTERNS = (
    re.compile(r"\bta gueule\b", re.IGNORECASE),
    re.compile(r"\bftg\b", re.IGNORECASE),
    re.compile(r"\bconnard\b", re.IGNORECASE),
    re.compile(r"\bpute\b", re.IGNORECASE),
    re.compile(r"\bencul[ée]\b", re.IGNORECASE),
)
_ONBOARDING_PROFILE_META_ANSWER_PATTERNS = (
    re.compile(r"\btu\s+connais\b", re.IGNORECASE),
    re.compile(r"\bje\s+viens\s+de\s+te\s+le\s+dire\b", re.IGNORECASE),
    re.compile(r"\bje\s+l['’]ai\s+deja\s+dit\b", re.IGNORECASE),
    re.compile(r"\bt['’]es\s+s[ée]rieux\b", re.IGNORECASE),
    re.compile(r"\b(s[ée]rieux|hein|quoi)\b", re.IGNORECASE),
    re.compile(r"\b(blague|dr[oô]le)\b", re.IGNORECASE),
)
_ONBOARDING_PROFILE_META_VERBS = {"connais", "dis", "dit", "viens"}
_ONBOARDING_PROFILE_CANT_ANSWER_PATTERNS = (
    re.compile(r"^\s*je\s+sais\s+pas\s*[\!\.\?]*\s*$", re.IGNORECASE),
    re.compile(r"^\s*(non|nop|nan)\s*[\!\.\?]*\s*$", re.IGNORECASE),
    re.compile(r"^\s*j[’']?en\s+sais\s+rien\s*[\!\.\?]*\s*$", re.IGNORECASE),
)
_ONBOARDING_FIRST_NAME_ALLOWED_CHARS_PATTERN = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ'\- ]+$")
_ONBOARDING_LAST_NAME_ALLOWED_CHARS_PATTERN = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ'\- ]+$")
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
_ONBOARDING_BIRTH_DATE_YEAR_TYPO_PATTERN = re.compile(
    r"\b(\d{1,2})\s+([A-Za-zÀ-ÖØ-öø-ÿ]+)\s+(\d{5})\b",
    flags=re.IGNORECASE,
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


class _ProfileFieldExtractionLlmResponse(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    birth_date: str | None = None
    confidence: float = 0.0
    needs_clarification: bool = False
    clarification_question: str | None = None

_BANK_ACCOUNTS_REQUEST_HINTS = ("liste", "catégor", "depens", "dépens", "recett", "transaction", "relev")
_YES_VALUES = {
    "oui",
    "ouais",
    "yep",
    "yes",
    "y",
    "ok",
    "daccord",
    "confirm",
    "je confirme",
    "pret",
    "go",
    "cest pret",
    "c'est pret",
}
_NO_VALUES = {"non", "nope", "no", "n"}
_IMPORT_FILE_PROMPT = "Parfait. Envoie le fichier CSV du compte sélectionné."
_IMPORT_WAIT_READY_REPLY = (
    "Prochaine étape : importer un relevé mensuel.\n\n"
    "Idéalement, prends le mois le plus récent complet (un mois entier), comme ça ton premier rapport sera représentatif.\n\n"
    "Ton fichier CSV est prêt pour l’import ?"
)
_SYSTEM_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("income_salary", "Salaire"),
    ("income_other", "Autres revenus"),
    ("transfer_internal", "Transferts internes"),
    ("twint_p2p_pending", "À catégoriser (TWINT)"),
    ("banking_fees", "Frais bancaires"),
    ("savings", "Épargne & investissement"),
    ("gifts", "Cadeaux & dons"),
    ("food", "Alimentation"),
    ("housing", "Logement"),
    ("transport", "Transport"),
    ("health", "Santé"),
    ("leisure", "Loisirs"),
    ("shopping", "Shopping"),
    ("subscriptions", "Abonnements"),
    ("taxes", "Impôts"),
    ("insurance", "Assurance"),
    ("other", "Autres"),
)
_MERCHANT_CATEGORY_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("migros", "coop", "lidl", "aldi", "denner"), "Alimentation"),
    (("sbb", "cff", "tpg", "tl", "uber", "bolt"), "Transport"),
    (("swisscom", "salt", "sunrise", "spotify", "netflix", "apple", "google"), "Abonnements"),
    (("axa", "zurich", "helvetia", "mobiliar"), "Assurance"),
)
_FALLBACK_MERCHANT_CATEGORY = "Autres"
BANK_CODE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "ubs": ("ubs",),
    "raiffeisen": ("raiffeisen",),
    "revolut": ("revolut",),
}
_SHARED_EXPENSE_INTENT_KEYWORDS = (
    "partage",
    "partagée",
    "partagees",
    "partagées",
    "depenses partagees",
    "dépenses partagées",
    "valider partage",
    "partager ces depenses",
    "partager ces dépenses",
)

_ACCOUNT_LINK_SETUP_INTENT_KEYWORDS = (
    "lier compte",
    "dépenses communes",
    "depenses communes",
    "foyer",
    "partage externe",
    "partage hors app",
    "conjoint",
    "colocataire",
)

_SHARE_RULE_CATEGORY_ALIASES: dict[str, str] = {
    "logement": "housing",
    "housing": "housing",
    "alimentation": "food",
    "food": "food",
    "assurance": "insurance",
    "insurance": "insurance",
    "abonnements": "subscriptions",
    "subscriptions": "subscriptions",
    "transport": "transport",
    "loisirs": "hobbies",
    "hobbies": "hobbies",
    "habits": "habits",
    "gifts": "gifts",
    "cadeaux": "gifts",
}

_HOUSEHOLD_LINK_AUTO_PROMPT_REPLY = (
    "Maintenant que tu as vu ton premier rapport de dépenses, j’aimerais affiner: "
    "certaines dépenses sont-elles communes à ton foyer (conjoint/coloc) ?"
)

_ONBOARDING_PROFILE_INTRO_REPLY = (
    "Salut 👋\n\n"
    "Je suis ton assistant financier. Je t’aide à analyser tes dépenses et à construire un budget clair, automatiquement.\n\n"
    "On va faire ça en 3 étapes :\n"
    "1) créer ton profil\n"
    "2) ajouter ta banque\n"
    "3) importer un relevé récent pour générer ton premier rapport.\n\n"
    "Commençons 🙂"
)

_ONBOARDING_SESSION_RESUME_REPLY = "Salut 👋 Es-tu prêt à reprendre où nous en étions ?"

_ONBOARDING_SUBSTEP_TO_LOOP_ID: dict[str, str] = {
    "profile_collect": "onboarding.profile_collect",
    "profile_confirm": "onboarding.profile_confirm",
    "profile_fix_select": "onboarding.profile_fix_select",
    "bank_accounts_collect": "onboarding.bank_accounts_collect",
    "bank_accounts_confirm": "onboarding.bank_accounts_confirm",
    "import_select_account": "onboarding.import_select_account",
    "import_wait_ready": "onboarding.import_wait_ready",
    "categories_intro": "onboarding.categories_intro",
    "categories_bootstrap": "onboarding.categories_bootstrap",
    "report_offer": "onboarding.report",
    "report_sent": "onboarding.report",
}

_ONBOARDING_ROUTEABLE_SUBSTEPS: frozenset[str] = frozenset(_ONBOARDING_SUBSTEP_TO_LOOP_ID.keys())


def _build_implicit_loop_context(
    *,
    global_state: dict[str, Any] | None,
    state_dict: dict[str, Any],
    registry: LoopRegistry,
) -> LoopContext | None:
    """Infer an active loop context from onboarding global state when loop context is missing."""

    if not _is_valid_global_state(global_state):
        return None
    if global_state.get("mode") != "onboarding":
        return None

    onboarding_substep = global_state.get("onboarding_substep")
    if not isinstance(onboarding_substep, str):
        return None

    mapped_loop_id = _ONBOARDING_SUBSTEP_TO_LOOP_ID.get(onboarding_substep)
    if not isinstance(mapped_loop_id, str):
        return None

    mapped_loop = registry.get(mapped_loop_id)
    blocking = mapped_loop.blocking if mapped_loop is not None else True
    return LoopContext(loop_id=mapped_loop_id, step="active", data=dict(state_dict or {}), blocking=blocking)


def _compute_debug_loop(
    state_dict: dict[str, Any],
    global_state: dict[str, Any] | None,
    registry: LoopRegistry,
) -> dict[str, Any]:
    """Compute debug loop metadata from the final request state."""

    current_loop = parse_loop_context(state_dict.get("loop"))
    if current_loop is not None:
        return {
            "loop_id": current_loop.loop_id,
            "step": current_loop.step,
            "blocking": current_loop.blocking,
        }

    effective_global_state = state_dict.get("global_state") if isinstance(state_dict, dict) else None
    if not _is_valid_global_state(effective_global_state):
        effective_global_state = global_state

    if _is_valid_global_state(effective_global_state) and effective_global_state.get("mode") == "onboarding":
        onboarding_substep = effective_global_state.get("onboarding_substep")
        mapped_loop_id = (
            _ONBOARDING_SUBSTEP_TO_LOOP_ID.get(onboarding_substep)
            if isinstance(onboarding_substep, str)
            else None
        )
        if isinstance(mapped_loop_id, str):
            mapped = registry.get(mapped_loop_id)
            return {
                "loop_id": mapped_loop_id,
                "step": "start",
                "blocking": mapped.blocking if mapped is not None else True,
            }

    return {"loop_id": None, "step": None, "blocking": None}


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


def _build_import_file_ui_request(import_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a UI upload request payload to trigger file import."""

    ui_request: dict[str, Any] = {
        "type": "ui_request",
        "name": "import_file",
        "accepted_types": ["csv"],
    }
    if isinstance(import_context, dict):
        bank_account_id = import_context.get("bank_account_id") or import_context.get("selected_bank_account_id")
        if isinstance(bank_account_id, str) and bank_account_id.strip():
            ui_request["bank_account_id"] = bank_account_id.strip()
    return ui_request


def _should_prompt_household_link_setup(
    *,
    global_state: dict[str, Any] | None,
    profiles_repository: Any,
    profile_id: UUID,
) -> bool:
    """Return True when the onboarding flow should auto-start household link setup."""

    if not isinstance(global_state, dict):
        return False
    if global_state.get("mode") not in {"guided_budget", "onboarding"}:
        return False
    if global_state.get("onboarding_step") != "report":
        return False
    if global_state.get("household_link_prompted") is True:
        return False

    household_link_state = global_state.get("household_link")
    if isinstance(household_link_state, dict) and household_link_state.get("enabled") is True:
        return False

    if hasattr(profiles_repository, "get_active_household_link"):
        try:
            existing_link = profiles_repository.get_active_household_link(profile_id=profile_id)
        except Exception:
            logger.exception("household_link_auto_prompt_fetch_failed profile_id=%s", profile_id)
            existing_link = None
        if existing_link is not None:
            return False

    return True


def _build_profile_recap_reply(profile_fields: dict[str, Any]) -> str:
    """Build onboarding profile recap confirmation prompt."""
    return build_profile_recap_reply(profile_fields)


def _build_quick_reply_yes_no_ui_action() -> dict[str, Any]:
    """Return a UI payload instructing the client to show yes/no quick replies."""

    return {
        "type": "ui_action",
        "action": "quick_replies",
        "options": [
            {"id": "yes", "label": "✅", "value": "oui"},
            {"id": "no", "label": "❌", "value": "non"},
        ],
    }


def _build_quick_reply_profile_fix_ui_action() -> dict[str, Any]:
    """Return a UI payload for onboarding profile correction target selection."""

    return {
        "type": "ui_action",
        "action": "quick_replies",
        "options": [
            {"id": "fix_name", "label": "Prénom / Nom", "value": "corriger_nom"},
            {"id": "fix_birth", "label": "Date de naissance", "value": "corriger_date"},
        ],
    }


def _build_onboarding_intro_quick_replies_ui_action() -> dict[str, Any]:
    """Return quick replies for onboarding intro confirmation."""

    return {
        "type": "ui_action",
        "action": "quick_replies",
        "options": [{"id": "start", "label": "Allons-y !", "value": "allons-y"}],
    }


def _build_profile_name_form_ui_action(profile_fields: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return UI form payload for onboarding first/last name collection."""

    values = profile_fields if isinstance(profile_fields, dict) else {}
    first_name = str(values.get("first_name") or "").strip()
    last_name = str(values.get("last_name") or "").strip()

    return {
        "type": "ui_action",
        "action": "form",
        "form_id": "onboarding_profile_name",
        "title": "Ton profil",
        "fields": [
            {
                "id": "first_name",
                "label": "Prénom",
                "type": "text",
                "required": True,
                "placeholder": "Prénom",
                "default_value": first_name,
            },
            {
                "id": "last_name",
                "label": "Nom",
                "type": "text",
                "required": True,
                "placeholder": "Nom",
                "default_value": last_name,
            },
        ],
        "submit_label": "Valider",
    }


def _build_profile_birth_date_form_ui_action(profile_fields: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return UI form payload for onboarding birth date collection."""

    values = profile_fields if isinstance(profile_fields, dict) else {}
    birth_date = str(values.get("birth_date") or "").strip()

    return {
        "type": "ui_action",
        "action": "form",
        "form_id": "onboarding_profile_birth_date",
        "title": "Date de naissance",
        "fields": [
            {
                "id": "birth_date",
                "label": "Date de naissance",
                "type": "date",
                "required": True,
                "placeholder": "Date de naissance",
                "default_value": birth_date,
            }
        ],
        "submit_label": "Valider",
    }


def _build_bank_accounts_form_ui_action() -> dict[str, Any]:
    """Return UI form payload for onboarding bank account collection."""

    return {
        "type": "ui_action",
        "action": "form",
        "form_id": "onboarding_bank_accounts",
        "title": "Tes banques",
        "fields": [
            {
                "id": "selected_banks",
                "label": "Banques utilisées",
                "type": "multi_select",
                "required": True,
                "options": [{"id": option, "label": option, "value": option} for option in _ONBOARDING_BANK_ACCOUNT_PRESET_OPTIONS],
            },
        ],
        "submit_label": "Valider",
    }


def _extract_submitted_bank_account_names(values: dict[str, Any]) -> list[str]:
    raw_selected = values.get("selected_banks")
    selected: list[str] = []
    if isinstance(raw_selected, list):
        selected = [str(item).strip() for item in raw_selected if str(item).strip()]
    elif isinstance(raw_selected, str) and raw_selected.strip():
        try:
            parsed = json.loads(raw_selected)
        except json.JSONDecodeError:
            parsed = [part.strip() for part in raw_selected.split(",") if part.strip()]
        if isinstance(parsed, list):
            selected = [str(item).strip() for item in parsed if str(item).strip()]
        elif isinstance(parsed, str) and parsed.strip():
            selected = [part.strip() for part in parsed.split(",") if part.strip()]

    names = selected

    normalized: list[str] = []
    for name in names:
        matched, _ = extract_canonical_banks(name)
        normalized.extend(matched or [name])

    deduped: list[str] = []
    seen: set[str] = set()
    for name in normalized:
        key = _normalize_text(name)
        if key and key not in seen:
            deduped.append(name)
            seen.add(key)
    return deduped


def _build_bank_accounts_confirm_recap(accounts: list[dict[str, Any]]) -> str:
    return f"Tu as des comptes bancaires chez : {_format_accounts_for_reply(accounts)}.\n\nEst-ce bien correct ?"


def _build_profile_collect_ui_action(profile_fields: dict[str, Any]) -> dict[str, Any]:
    """Return the onboarding profile form action matching current missing fields."""

    has_first_name = _is_profile_field_completed(profile_fields.get("first_name"))
    has_last_name = _is_profile_field_completed(profile_fields.get("last_name"))
    if not has_first_name or not has_last_name:
        return _build_profile_name_form_ui_action(profile_fields)
    return _build_profile_birth_date_form_ui_action(profile_fields)


def _update_profile_fields_safe(
    *,
    profiles_repository: Any,
    profile_id: UUID,
    user_id: UUID,
    set_dict: dict[str, Any],
) -> Any:
    """Update profile fields with backward-compatible user_id fallback."""

    try:
        return profiles_repository.update_profile_fields(
            profile_id=profile_id,
            user_id=user_id,
            set_dict=set_dict,
        )
    except TypeError:
        return profiles_repository.update_profile_fields(
            profile_id=profile_id,
            set_dict=set_dict,
        )


def _parse_ui_form_submit_message(message: str) -> dict[str, Any] | None:
    """Parse deterministic UI form-submit envelope when present.

    Expected message format:
    - free-form human line for transcript readability
    - followed by ``
__ui_form_submit__:{"form_id":"...","values":{...}}``

    Example:
    ``Prénom: Ada, Nom: Lovelace
__ui_form_submit__:{"form_id":"onboarding_profile_name","values":{"first_name":"Ada","last_name":"Lovelace"}}``
    """

    if not isinstance(message, str):
        return None

    marker_index = message.find(f"\n{_UI_FORM_SUBMIT_PREFIX}")
    if marker_index >= 0:
        payload_start = marker_index + 1 + len(_UI_FORM_SUBMIT_PREFIX)
    else:
        marker_index = message.find(_UI_FORM_SUBMIT_PREFIX)
        if marker_index < 0:
            return None
        payload_start = marker_index + len(_UI_FORM_SUBMIT_PREFIX)

    raw_payload = message[payload_start:].strip()
    if not raw_payload:
        raise ValueError("missing_form_payload")
    parsed = json.loads(raw_payload)
    if not isinstance(parsed, dict):
        raise ValueError("invalid_form_payload")
    return parsed


def _extract_required_form_value(values: dict[str, Any], field_id: str) -> str:
    """Return required form string value or raise for missing/invalid payload."""

    raw = values.get(field_id)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"missing_{field_id}")
    return raw.strip()


def _build_open_pdf_ui_request(url: str) -> dict[str, str]:
    """Return the legacy-compatible UI request payload for opening a PDF report."""

    return {
        "type": "ui_request",
        "name": "open_pdf_report",
        "url": url,
    }


def _build_import_done_reply(total_transactions: int | None) -> str:
    """Build final import completion text with optional transaction count."""

    transactions_fragment = (
        f"tes {total_transactions} transactions"
        if isinstance(total_transactions, int) and total_transactions >= 0
        else "tes transactions"
    )
    return (
        "Import terminé ✅\n\n"
        f"Je viens de classer {transactions_fragment} et de générer ton premier rapport financier.\n\n"
        "Es-tu prêt à le découvrir ?"
    )


def _normalize_user_text_for_matching(value: str) -> str:
    return _normalize_text_basic(value)


def _normalize_text_basic(s: str) -> str:
    """Normalize user text for deterministic command parsing."""

    normalized = unicodedata.normalize("NFKD", s.replace("’", "'").replace("`", "'"))
    ascii_only = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", ascii_only.lower()).strip()


def _parse_category_norm_from_text(fragment: str) -> str | None:
    """Resolve one supported category alias to canonical category key."""

    normalized = _normalize_text_basic(fragment)
    category = _SHARE_RULE_CATEGORY_ALIASES.get(normalized)
    if category is not None:
        return category

    compact = re.sub(r"[^a-z]", "", normalized)
    for alias, category_norm in _SHARE_RULE_CATEGORY_ALIASES.items():
        alias_compact = re.sub(r"[^a-z]", "", alias)
        if compact == alias_compact:
            return category_norm
    return None


def parse_share_rule_command(message: str) -> dict[str, Any] | None:
    """Parse deterministic FR share-rule chat command."""

    normalized = _normalize_text_basic(message)

    share_patterns = (
        re.compile(r"^toutes mes (depenses|transactions) (?P<cat>.+?) sont partagees$"),
        re.compile(r"^(partage|toujours partager) (?P<cat>.+)$"),
    )
    for pattern in share_patterns:
        match = pattern.match(normalized)
        if not match:
            continue
        category_norm = _parse_category_norm_from_text(str(match.group("cat") or ""))
        if category_norm is None:
            return {"error": "category_unknown"}
        return {
            "rule_type": "category",
            "rule_key": category_norm,
            "action": "force_share",
            "boost_value": None,
        }

    exclude_patterns = (
        re.compile(r"^ne (jamais )?partage (pas )?(?P<cat>.+)$"),
        re.compile(r"^ne partage pas (?P<cat>.+)$"),
        re.compile(r"^stop partage (?P<cat>.+)$"),
    )
    for pattern in exclude_patterns:
        match = pattern.match(normalized)
        if not match:
            continue
        category_norm = _parse_category_norm_from_text(str(match.group("cat") or ""))
        if category_norm is None:
            return {"error": "category_unknown"}
        return {
            "rule_type": "category",
            "rule_key": category_norm,
            "action": "force_exclude",
            "boost_value": None,
        }

    boost_patterns = (
        re.compile(r"^boost( partage)? (?P<cat>.+?) (?P<val>[+-]?\d+(\.\d+)?)$"),
        re.compile(r"^augmente partage (?P<cat>.+?) (?P<val>[+-]?\d+(\.\d+)?)$"),
    )
    for pattern in boost_patterns:
        match = pattern.match(normalized)
        if not match:
            continue
        category_norm = _parse_category_norm_from_text(str(match.group("cat") or ""))
        if category_norm is None:
            return {"error": "category_unknown"}
        raw_val = str(match.group("val") or "").strip()
        try:
            boost_value = Decimal(raw_val)
        except InvalidOperation:
            return {"error": "invalid_boost"}
        if boost_value <= Decimal("0") or boost_value > Decimal("1"):
            return {"error": "invalid_boost"}
        return {
            "rule_type": "category",
            "rule_key": category_norm,
            "action": "boost",
            "boost_value": boost_value,
        }

    if normalized.startswith("boost") or normalized.startswith("augmente partage"):
        return {"error": "invalid_boost"}

    return None


def _is_shared_expense_validation_intent(message: str) -> bool:
    normalized = _normalize_user_text_for_matching(message)
    return any(keyword in normalized for keyword in _SHARED_EXPENSE_INTENT_KEYWORDS)


def _is_account_link_setup_intent(message: str) -> bool:
    normalized = _normalize_user_text_for_matching(message)
    return any(keyword in normalized for keyword in _ACCOUNT_LINK_SETUP_INTENT_KEYWORDS)


def _execute_account_link_setup_task(
    *,
    user_message: str,
    active_task: dict[str, Any] | None,
    state_dict: dict[str, Any] | None,
    profile_id: UUID | None = None,
    profiles_repository: Any | None = None,
    debug_enabled: bool = False,
) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    """Advance deterministic account-link setup flow and optionally persist settings in chat state."""

    normalized = _normalize_user_text_for_matching(user_message)
    task = dict(active_task) if isinstance(active_task, dict) else {"type": "account_link_setup", "step": "ask_has_shared_expenses", "draft": {}}
    draft = task.get("draft") if isinstance(task.get("draft"), dict) else {}
    step = str(task.get("step") or "ask_has_shared_expenses")

    if step == "ask_has_shared_expenses":
        if _is_no(normalized):
            return "Ok, on laisse le partage désactivé pour l’instant.", None, state_dict, None
        task["step"] = "ask_link_type"
        task["draft"] = draft
        return "Tu veux lier avec un compte interne dans l’app ou une personne externe (hors app) ?", task, state_dict, None

    if step == "ask_link_type":
        if "externe" in normalized or "hors app" in normalized:
            draft["link_type"] = "external"
            task["step"] = "ask_label"
            task["draft"] = draft
            return "Quel libellé veux-tu utiliser pour cette personne (ex: Conjoint, Colocataire) ?", task, state_dict, None
        if "interne" in normalized:
            draft["link_type"] = "internal"
            task["step"] = "ask_default_split"
            task["draft"] = draft
            return "Quel ratio par défaut veux-tu appliquer pour l’autre personne ? (ex: 50/50)", task, state_dict, None
        return "Réponds ‘interne’ ou ‘externe’.", task, state_dict, None

    if step == "ask_label":
        label = user_message.strip()
        if not label:
            return "Je n’ai pas compris le libellé. Ex: Conjoint.", task, state_dict, None
        draft["other_party_label"] = label
        task["step"] = "ask_default_split"
        task["draft"] = draft
        return "Parfait. Ratio par défaut ? (ex: 50/50, Entrée vide = 50/50)", task, state_dict, None

    if step == "ask_default_split":
        ratio_other = Decimal("0.5")
        split_match = re.search(r"(\d{1,3})\s*/\s*(\d{1,3})", normalized)
        if split_match:
            left = int(split_match.group(1))
            right = int(split_match.group(2))
            if left + right > 0:
                ratio_other = (Decimal(str(right)) / Decimal(str(left + right))).quantize(Decimal("0.0001"))

        link_state = {
            "link_type": str(draft.get("link_type") or "external"),
            "other_profile_id": draft.get("other_profile_id"),
            "other_party_label": draft.get("other_party_label"),
            "default_split_ratio_other": str(ratio_other),
            "enabled": True,
        }
        normalized_link_type = link_state["link_type"]
        other_profile_id = link_state.get("other_profile_id")
        other_party_label = link_state.get("other_party_label")
        other_party_email = draft.get("other_party_email")
        default_split_ratio_other = link_state["default_split_ratio_other"]

        if profiles_repository is not None and profile_id is not None and hasattr(profiles_repository, "upsert_household_link"):
            try:
                persisted_link_state = profiles_repository.upsert_household_link(
                    profile_id=profile_id,
                    link_type=normalized_link_type,
                    other_profile_id=(UUID(str(other_profile_id)) if other_profile_id else None),
                    other_party_label=other_party_label,
                    other_party_email=other_party_email,
                    default_split_ratio_other=default_split_ratio_other,
                )
                if isinstance(persisted_link_state, dict):
                    link_state = {
                        "link_type": str(persisted_link_state.get("link_type") or link_state["link_type"]),
                        "other_profile_id": persisted_link_state.get("other_profile_id"),
                        "other_party_label": persisted_link_state.get("other_party_label"),
                        "other_party_email": persisted_link_state.get("other_party_email"),
                        "default_split_ratio_other": str(
                            persisted_link_state.get("default_split_ratio_other") or link_state["default_split_ratio_other"]
                        ),
                        "enabled": True,
                    }
            except Exception as exc:
                error_id = _new_short_error_id("HHL")
                context_payload = {
                    "step": "shared_expense_link_setup",
                    "link_type": normalized_link_type,
                    "other_party_label": other_party_label,
                    "has_other_profile_id": bool(other_profile_id),
                    "has_other_party_email": bool(other_party_email),
                    "default_split_ratio_other": default_split_ratio_other,
                }
                logger.error(
                    "account_link_upsert_failed error_id=%s profile_id=%s",
                    error_id,
                    profile_id,
                    exc_info=exc,
                    extra={"error_id": error_id, "payload_synthese": context_payload},
                )
                generic_reply = "Impossible d’enregistrer la configuration pour le moment (erreur base de données). Réessaie dans un instant."
                tool_result = None
                if debug_enabled:
                    db_error: dict[str, Any] | None = None
                    if isinstance(exc, SupabaseRequestError):
                        error_json = exc.error_json or {}
                        db_error = {
                            "status_code": exc.status_code,
                            "code": error_json.get("code"),
                            "details": error_json.get("details"),
                            "hint": error_json.get("hint"),
                            "message": error_json.get("message") or exc.raw_text,
                        }
                    tool_result = {
                        "type": "error",
                        "where": "upsert_household_link",
                        "message": str(exc),
                        "context": context_payload,
                        "db_error": db_error,
                        "error_id": error_id,
                    }
                return generic_reply, task, state_dict, tool_result

        updated_state = dict(state_dict) if isinstance(state_dict, dict) else {}
        global_state = updated_state.get("global_state") if isinstance(updated_state.get("global_state"), dict) else {}
        global_state = dict(global_state)
        global_state["household_link"] = link_state
        updated_state["global_state"] = global_state

        if profile_id is None:
            return "Configuration enregistrée ✅. Tu peux maintenant utiliser le partage des dépenses, y compris en mode externe.", None, updated_state, None

        try:
            _seed_shared_expense_suggestions_after_link_setup(profile_id=profile_id, link_state=link_state)
            reply_validation, shared_task = handle_shared_expenses_validation_request(profile_id=profile_id)
        except HTTPException:
            return "Configuration enregistrée ✅. Tu peux maintenant utiliser le partage des dépenses, y compris en mode externe.", None, updated_state, None
        if shared_task is None:
            return (
                "Configuration enregistrée ✅. Je n’ai pas trouvé de dépenses à proposer pour le partage pour l’instant. "
                "Tu peux me dire 'valider partage' plus tard.",
                None,
                updated_state,
                None,
            )
        return f"Configuration enregistrée ✅. Voici les premières dépenses à valider :\n\n{reply_validation}", shared_task, updated_state, None

    return "Je reprends la configuration du partage. Interne ou externe ?", {"type": "account_link_setup", "step": "ask_link_type", "draft": draft}, state_dict, None


def _seed_shared_expense_suggestions_after_link_setup(*, profile_id: UUID, link_state: dict[str, Any]) -> int:
    """Generate first deterministic shared-expense suggestions after link setup."""

    try:
        repository = _get_shared_expenses_repository_or_501()
    except HTTPException:
        return 0

    supabase_client = SupabaseClient(
        settings=SupabaseSettings(
            url=_config.supabase_url(),
            service_role_key=_config.supabase_service_role_key(),
            anon_key=_config.supabase_anon_key(),
        )
    )
    try:
        return generate_initial_shared_expense_suggestions(
            profile_id=profile_id,
            household_link=link_state,
            shared_expenses_repository=repository,
            supabase_client=supabase_client,
            share_rules_repository=_try_get_share_rules_repository(),
            limit=40,
        )
    except RuntimeError:
        logger.exception("shared_expense_seed_after_link_setup_failed profile_id=%s", profile_id)
        return 0


def fetch_transactions_snapshot(profile_id: UUID, transaction_ids: list[UUID]) -> dict[UUID, dict[str, str | None]]:
    """Return transaction details keyed by transaction id for shared-expense confirmation."""

    if not transaction_ids:
        return {}

    supabase_client = SupabaseClient(
        settings=SupabaseSettings(
            url=_config.supabase_url(),
            service_role_key=_config.supabase_service_role_key(),
            anon_key=_config.supabase_anon_key(),
        )
    )
    in_values = ",".join(str(transaction_id) for transaction_id in transaction_ids)
    try:
        rows, _ = supabase_client.get_rows(
            table="releves_bancaires",
            query={
                "select": "id,date,montant,devise,payee,libelle",
                "profile_id": f"eq.{profile_id}",
                "id": f"in.({in_values})",
                "limit": max(1, len(transaction_ids)),
            },
            with_count=False,
            use_anon_key=False,
        )
    except RuntimeError:
        logger.exception("shared_expense_transaction_snapshot_failed profile_id=%s", profile_id)
        return {}

    snapshot: dict[UUID, dict[str, str | None]] = {}
    for row in rows:
        raw_id = row.get("id")
        try:
            row_id = raw_id if isinstance(raw_id, UUID) else UUID(str(raw_id))
        except (TypeError, ValueError):
            continue
        snapshot[row_id] = {
            "date": str(row.get("date") or ""),
            "montant": str(row.get("montant") or ""),
            "devise": str(row.get("devise") or "") if row.get("devise") is not None else None,
            "payee": str(row.get("payee") or "") if row.get("payee") is not None else None,
            "libelle": str(row.get("libelle") or "") if row.get("libelle") is not None else None,
        }
    return snapshot


def _build_shared_expense_confirmation_reply(suggestions: list[dict[str, Any]]) -> str:
    lines = [f"J’ai {len(suggestions)} dépenses à valider pour le partage :"]
    for row in suggestions:
        merchant = str(row.get("merchant") or f"transaction {row.get('transaction_id')}")
        date_value = str(row.get("date") or "?")
        amount_value = str(row.get("amount") or "?")
        currency = str(row.get("currency") or "CHF")
        split_value = Decimal(str(row.get("suggested_split_ratio_other") or "0.5")) * Decimal("100")
        split_text = f"autre: {int(split_value)}%"
        target_text = str(row.get("target_label") or "autre")
        lines.append(f"{row['index']}) {date_value} — {merchant} — {amount_value} {currency} — {split_text} — cible: {target_text}")
    lines.append(
        "Réponds: ‘oui tout’, ‘non tout’, ‘oui 1 et 2’, ‘non 2’, ‘split 2 60/40’ (règle split: toi/autre)."
    )
    return "\n".join(lines)


def handle_shared_expenses_validation_request(*, profile_id: UUID) -> tuple[str, dict[str, Any] | None]:
    """Return assistant message and active confirmation task snapshot for shared expenses."""

    repository = _get_shared_expenses_repository_or_501()
    suggestions = repository.list_shared_expense_suggestions(profile_id=profile_id, status="pending", limit=50)
    if not suggestions:
        return "Il n’y a rien à valider pour le partage pour le moment.", None

    transaction_ids = [row.transaction_id for row in suggestions]
    transactions_snapshot = fetch_transactions_snapshot(profile_id, transaction_ids)

    snapshot_rows: list[dict[str, Any]] = []
    for index, suggestion in enumerate(suggestions, start=1):
        transaction_row = transactions_snapshot.get(suggestion.transaction_id, {})
        raw_amount = transaction_row.get("montant") if isinstance(transaction_row, dict) else None
        try:
            amount = abs(Decimal(str(raw_amount or "0"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError):
            amount = Decimal("0.00")
        merchant = ""
        if isinstance(transaction_row, dict):
            merchant = str(transaction_row.get("payee") or transaction_row.get("libelle") or "").strip()
        snapshot_rows.append(
            {
                "index": index,
                "suggestion_id": str(suggestion.id),
                "transaction_id": str(suggestion.transaction_id),
                "date": str((transaction_row or {}).get("date") or ""),
                "merchant": merchant,
                "amount": f"{amount:.2f}",
                "currency": str((transaction_row or {}).get("devise") or "CHF"),
                "suggested_split_ratio_other": str(suggestion.suggested_split_ratio_other),
                "suggested_to_profile_id": str(suggestion.suggested_to_profile_id) if suggestion.suggested_to_profile_id else None,
                "other_party_label": suggestion.other_party_label,
                "target_label": suggestion.other_party_label or ("autre (hors app)" if suggestion.suggested_to_profile_id is None else "autre"),
            }
        )

    active_task = {
        "type": "shared_expense_confirm",
        "created_at": datetime.now().isoformat(),
        "suggestions": snapshot_rows,
    }
    return _build_shared_expense_confirmation_reply(snapshot_rows), active_task


def _extract_indices_from_message(message: str) -> list[int]:
    return [int(token) for token in re.findall(r"\d+", message)]


def parse_shared_expense_confirmation(user_message: str, suggestions_snapshot: list[dict[str, Any]]) -> dict[str, Any]:
    """Parse deterministic shared-expense confirmation commands into executable actions."""

    normalized = _normalize_user_text_for_matching(user_message)
    available = {int(row["index"]) for row in suggestions_snapshot if isinstance(row.get("index"), int)}

    split_match = re.search(r"(?:split\s+)?(\d+)\s+(\d{1,3})\s*/\s*(\d{1,3})", normalized)
    if split_match:
        index_value = int(split_match.group(1))
        left = int(split_match.group(2))
        right = int(split_match.group(3))
        if index_value not in available or left + right == 0:
            return {"kind": "invalid"}
        ratio_other = (Decimal(str(right)) / Decimal(str(left + right))).quantize(Decimal("0.0001"))
        return {"kind": "split_apply", "indices": [index_value], "ratio_other": ratio_other}

    if any(phrase in normalized for phrase in ("oui tout", "ok tout", "confirme tout", "applique tout")):
        return {"kind": "apply_all", "indices": sorted(available)}
    if any(phrase in normalized for phrase in ("non tout", "rejette tout", "refuse tout")):
        return {"kind": "dismiss_all", "indices": sorted(available)}

    indices = [index for index in _extract_indices_from_message(normalized) if index in available]
    if not indices:
        return {"kind": "invalid"}

    if any(keyword in normalized for keyword in ("oui", "ok", "applique", "confirme")):
        return {"kind": "apply_subset", "indices": sorted(set(indices))}
    if any(keyword in normalized for keyword in ("non", "rejette", "refuse")):
        return {"kind": "dismiss_subset", "indices": sorted(set(indices))}
    return {"kind": "invalid"}


def _execute_shared_expense_confirmation_actions(
    *,
    profile_id: UUID,
    active_task: dict[str, Any],
    user_message: str,
) -> tuple[str, dict[str, Any] | None]:
    suggestions_snapshot = active_task.get("suggestions") if isinstance(active_task, dict) else None
    if not isinstance(suggestions_snapshot, list) or not suggestions_snapshot:
        return "Je n’ai plus de suggestion active à valider.", None

    parsed = parse_shared_expense_confirmation(user_message, suggestions_snapshot)
    if parsed.get("kind") == "invalid":
        return (
            "Je n’ai pas compris. Réponds avec ‘oui tout’, ‘non tout’, ‘oui 1 et 3’, ‘non 2’ ou ‘split 2 60/40’.",
            active_task,
        )

    by_index = {
        int(item["index"]): item
        for item in suggestions_snapshot
        if isinstance(item, dict) and isinstance(item.get("index"), int)
    }
    repository = _get_shared_expenses_repository_or_501()
    applied: list[int] = []
    dismissed: list[int] = []
    errors: list[int] = []
    ratio_other = parsed.get("ratio_other")

    for index in parsed.get("indices", []):
        row = by_index.get(index)
        if row is None:
            continue
        try:
            suggestion_id = UUID(str(row["suggestion_id"]))
        except (TypeError, ValueError, KeyError):
            errors.append(index)
            continue

        should_apply = parsed["kind"] in {"apply_all", "apply_subset", "split_apply"}
        if should_apply:
            try:
                amount = Decimal(str(row.get("amount") or "0"))
                effective_ratio_other = Decimal(str(ratio_other or row.get("suggested_split_ratio_other") or "0.5"))
                amount_to_apply = (abs(amount) * effective_ratio_other).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                repository.create_shared_expense_from_suggestion(
                    profile_id=profile_id,
                    suggestion_id=suggestion_id,
                    amount=amount_to_apply,
                )
                applied.append(index)
            except Exception:
                logger.exception("shared_expense_apply_from_chat_failed profile_id=%s suggestion_id=%s", profile_id, suggestion_id)
                errors.append(index)
            continue

        try:
            repository.mark_suggestion_status(
                profile_id=profile_id,
                suggestion_id=suggestion_id,
                status="dismissed",
                error="dismissed via chat",
            )
            dismissed.append(index)
        except Exception:
            logger.exception("shared_expense_dismiss_from_chat_failed profile_id=%s suggestion_id=%s", profile_id, suggestion_id)
            errors.append(index)

    treated = set(applied + dismissed)
    remaining = [row for row in suggestions_snapshot if int(row.get("index", -1)) not in treated]
    if not remaining:
        return f"Terminé. Appliqué: {len(applied)}, Rejeté: {len(dismissed)}, Erreurs: {len(errors)}", None

    updated_task = dict(active_task)
    updated_task["suggestions"] = remaining
    summary = f"Appliqué: {len(applied)}, Rejeté: {len(dismissed)}, Erreurs: {len(errors)}."
    return f"{summary}\n\n{_build_shared_expense_confirmation_reply(remaining)}", updated_task




def _is_internal_transfer_payload(row: dict[str, Any]) -> bool:
    """Return True when row payload represents an internal transfer transaction."""

    meta = row.get("meta")
    if isinstance(meta, dict) and str(meta.get("tx_kind") or "").strip().lower() == "transfer_internal":
        return True

    category = row.get("categorie")
    return isinstance(category, str) and category.strip().lower() in {"transferts internes", "transfert interne"}

def _build_spending_pdf_url(
    *,
    month: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    bank_account_id: str | None = None,
) -> str:
    """Build relative spending report endpoint URL with optional period filters."""

    bank_account_query = ""
    if isinstance(bank_account_id, str) and bank_account_id.strip():
        bank_account_query = f"&bank_account_id={bank_account_id.strip()}"

    if isinstance(month, str) and month.strip():
        return f"/finance/reports/spending.pdf?month={month.strip()}{bank_account_query}"

    if isinstance(start_date, str) and isinstance(end_date, str) and start_date.strip() and end_date.strip():
        return f"/finance/reports/spending.pdf?start_date={start_date.strip()}&end_date={end_date.strip()}{bank_account_query}"

    return "/finance/reports/spending.pdf"


def _resolve_last_report_filters(state_dict: dict[str, Any] | None) -> tuple[str | None, str | None, str | None]:
    """Resolve report date range and bank account filters from conversation state."""

    if not isinstance(state_dict, dict):
        return None, None, None

    last_import = state_dict.get("last_import")
    if isinstance(last_import, dict):
        date_range = last_import.get("date_range")
        if isinstance(date_range, dict):
            start_raw = date_range.get("start_date")
            end_raw = date_range.get("end_date")
            if isinstance(start_raw, str) and isinstance(end_raw, str) and start_raw.strip() and end_raw.strip():
                bank_account = last_import.get("bank_account_id")
                bank_account_value = bank_account.strip() if isinstance(bank_account, str) and bank_account.strip() else None
                return start_raw.strip(), end_raw.strip(), bank_account_value

    last_query = state_dict.get("last_query")
    if isinstance(last_query, dict):
        filters = last_query.get("filters") if isinstance(last_query.get("filters"), dict) else None
        date_range = filters.get("date_range") if isinstance(filters, dict) else None
        if isinstance(date_range, dict):
            start_raw = date_range.get("start_date")
            end_raw = date_range.get("end_date")
            if isinstance(start_raw, str) and isinstance(end_raw, str) and start_raw.strip() and end_raw.strip():
                bank_account = filters.get("bank_account_id") if isinstance(filters, dict) else None
                bank_account_value = bank_account.strip() if isinstance(bank_account, str) and bank_account.strip() else None
                return start_raw.strip(), end_raw.strip(), bank_account_value

    return None, None, None


def _extract_import_date_range(result: dict[str, Any]) -> dict[str, str] | None:
    """Infer import date range from explicit fields or preview rows."""

    start_raw = result.get("import_start_date")
    end_raw = result.get("import_end_date")
    if isinstance(start_raw, str) and isinstance(end_raw, str):
        try:
            start_value = date.fromisoformat(start_raw)
            end_value = date.fromisoformat(end_raw)
            return {
                "start": min(start_value, end_value).isoformat(),
                "end": max(start_value, end_value).isoformat(),
            }
        except ValueError:
            pass

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


def _is_tool_error_payload(result: Any) -> bool:
    """Return whether a JSON-encoded payload looks like a ToolError."""

    if not isinstance(result, dict):
        return False
    code = result.get("code")
    message = result.get("message")
    return isinstance(code, str) and bool(code.strip()) and isinstance(message, str) and bool(message.strip())


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


def _is_birth_date_field_completed(value: Any) -> bool:
    """Return True when birth_date is a non-empty valid ISO date (YYYY-MM-DD)."""

    if not isinstance(value, str):
        return False

    normalized_value = value.strip()
    if not normalized_value:
        return False
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized_value):
        return False

    try:
        parsed_date = date.fromisoformat(normalized_value)
    except ValueError:
        return False
    return parsed_date.isoformat() == normalized_value


def _compute_bootstrap_global_state(profile_fields: dict[str, Any]) -> dict[str, Any]:
    """Compute initial global state from profile completeness."""

    is_profile_complete = _is_profile_complete(profile_fields)
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
        "onboarding_substep": "profile_intro",
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

    for field_name in _PROFILE_COMPLETION_FIELDS:
        field_value = profile_fields.get(field_name)
        if field_name == "birth_date":
            if not _is_birth_date_field_completed(field_value):
                return False
            continue
        if not _is_profile_field_completed(field_value):
            return False
    return True


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
        "profile": {
            "profile_intro",
            "profile_collect",
            "profile_confirm",
            "profile_fix_select",
            "profile_fix_name",
            "profile_fix_birth_date",
        },
        "bank_accounts": {"bank_accounts_collect", "bank_accounts_confirm", "bank_accounts_fix_select"},
        "import": {"import_select_account", "import_wait_ready"},
        "categories": {"categories_intro", "categories_bootstrap"},
        "report": {"report_offer", "report_sent"},
    }
    default_substep_by_step = {
        "profile": "profile_intro",
        "bank_accounts": "bank_accounts_collect",
        "import": "import_wait_ready",
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
    normalized = _normalize_text(message)
    return normalized in _YES_VALUES or "✅" in message


def _is_no(message: str) -> bool:
    normalized = _normalize_text(message)
    return normalized in _NO_VALUES or "❌" in message


def _is_allons_y(message: str) -> bool:
    normalized = _normalize_text(message)
    return normalized in {"allons-y", "allons y", "allons_y"}


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
        observed_alias_norm = normalize_merchant_alias(observed_alias)
        if not observed_alias_norm:
            skipped_count += 1
            continue

        raw_meta = row.get("meta")
        if isinstance(raw_meta, dict):
            meta: dict[str, Any] = raw_meta
        elif isinstance(raw_meta, str):
            try:
                parsed_meta = json.loads(raw_meta)
            except Exception:
                parsed_meta = {}
            meta = parsed_meta if isinstance(parsed_meta, dict) else {}
        else:
            meta = {}
        observed_alias_key_norm = " ".join(str(meta.get("observed_alias_key_norm") or "").split())
        dedup_alias_norm = (
            normalize_merchant_alias(observed_alias_key_norm)
            if observed_alias_key_norm
            else observed_alias_norm
        )
        if not dedup_alias_norm:
            skipped_count += 1
            continue

        try:
            releve_id = UUID(str(releve_id_raw))
            entity = profiles_repository.find_merchant_entity_by_alias_norm(alias_norm=dedup_alias_norm)
            if not entity and dedup_alias_norm != observed_alias_norm:
                entity = profiles_repository.find_merchant_entity_by_alias_norm(alias_norm=observed_alias_norm)
            if not entity:
                if hasattr(profiles_repository, "create_pending_map_alias_suggestion"):
                    suggestion_created = bool(
                        profiles_repository.create_pending_map_alias_suggestion(
                            profile_id=profile_id,
                            observed_alias=observed_alias,
                            observed_alias_norm=observed_alias_norm,
                            merchant_key_norm=dedup_alias_norm,
                            rationale=(
                                "Alias inconnu lors de l'import; nécessite normalisation/"
                                "canonicalisation et catégorisation LLM."
                            ),
                            confidence=0.0,
                        )
                    )
                    if suggestion_created:
                        suggestions_created_count += 1
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


def _detect_bank_account_for_import(
    *,
    filename: str,
    file_bytes: bytes | None,
    existing_accounts: list[dict[str, Any]],
) -> dict[str, Any] | str | None:
    """Detect target bank account from CSV structure first, then fallback to filename."""

    normalized_filename = _normalize_text(filename)
    preview_bytes = (file_bytes or b"")[:8192]
    preview_text = preview_bytes.decode("utf-8", errors="ignore") if preview_bytes else ""
    is_csv_filename = normalized_filename.endswith(".csv")

    has_csv_like_header = False
    if not is_csv_filename and b"\x00" in preview_bytes:
        has_csv_like_header = False
    elif not is_csv_filename and preview_text:
        for line in preview_text.splitlines()[:5]:
            stripped_line = line.strip()
            if not stripped_line:
                continue
            if "," in stripped_line:
                columns = [col.strip() for col in stripped_line.split(",")]
                letter_columns = sum(1 for col in columns if col and re.search(r"[A-Za-z]", col))
                if len(columns) >= 3 and all(columns) and letter_columns >= 2:
                    has_csv_like_header = True
                    break
            if ";" in stripped_line:
                columns = [col.strip() for col in stripped_line.split(";")]
                letter_columns = sum(1 for col in columns if col and re.search(r"[A-Za-z]", col))
                if len(columns) >= 3 and all(columns) and letter_columns >= 2:
                    has_csv_like_header = True
                    break

    can_run_csv_detection = is_csv_filename or has_csv_like_header
    detected_bank_code = detect_bank_from_csv_bytes(file_bytes or b"") if can_run_csv_detection and file_bytes else None
    if detected_bank_code:
        bank_keywords = BANK_CODE_KEYWORDS.get(detected_bank_code, (detected_bank_code,))
        matched_accounts: list[dict[str, Any]] = []
        for account in existing_accounts:
            account_name = str(account.get("name") or "").strip()
            if not account_name:
                continue
            normalized_name = _normalize_text(account_name)
            if any(keyword in normalized_name for keyword in bank_keywords):
                matched_accounts.append(account)

        logger.info(
            "bank_detection_result bank_code=%s matched_account_count=%s",
            detected_bank_code,
            len(matched_accounts),
        )

        if len(matched_accounts) == 1:
            return matched_accounts[0]
        if len(matched_accounts) > 1:
            return "ambiguous"

    logger.debug(
        "bank_detection_result bank_code=%s matched_account_count=%s",
        detected_bank_code,
        0,
    )

    substring_matches: list[dict[str, Any]] = []
    for account in existing_accounts:
        account_name = str(account.get("name") or "").strip()
        if not account_name:
            continue
        normalized_name = _normalize_text(account_name)
        if normalized_name and normalized_name in normalized_filename:
            substring_matches.append(account)

    if len(substring_matches) == 1:
        return substring_matches[0]
    if len(substring_matches) > 1:
        return "ambiguous"
    if len(existing_accounts) == 1:
        return existing_accounts[0]
    if len(existing_accounts) > 1:
        return "ambiguous"
    return None


def _detect_bank_code_from_import_files(files: list[dict[str, Any]]) -> str | None:
    """Detect bank code from import files using existing CSV bank detector."""

    for file in files:
        if not isinstance(file, dict):
            continue
        filename = str(file.get("filename") or "").strip()
        if filename and not filename.lower().endswith(".csv"):
            continue
        content_base64 = file.get("content_base64")
        if not isinstance(content_base64, str) or not content_base64:
            continue
        try:
            preview_bytes = base64.b64decode(content_base64, validate=False)[:65536]
        except Exception:
            logger.warning("import_bank_detection_decode_failed filename=%s", filename)
            continue
        detected_bank_code = detect_bank_from_csv_bytes(preview_bytes)
        if isinstance(detected_bank_code, str) and detected_bank_code.strip():
            return detected_bank_code.strip().lower()
    return None


def _resolve_bank_account_id_from_bank_code(
    profiles_repository: Any,
    profile_id: UUID,
    bank_code: str,
) -> str | None:
    """Resolve one account id for a detected bank code; raise on ambiguity."""

    if not bank_code.strip() or not hasattr(profiles_repository, "list_bank_accounts"):
        return None

    normalized_bank_code = _normalize_text(bank_code)
    bank_keywords = BANK_CODE_KEYWORDS.get(normalized_bank_code, (normalized_bank_code,))
    accounts = profiles_repository.list_bank_accounts(profile_id=profile_id)

    matched_account_ids: list[str] = []
    for account in accounts:
        if not isinstance(account, dict):
            continue
        account_id = str(account.get("id") or "").strip()
        account_name = str(account.get("name") or "").strip()
        if not account_id or not account_name:
            continue
        normalized_name = _normalize_text(account_name)
        if any(keyword in normalized_name for keyword in bank_keywords):
            matched_account_ids.append(account_id)

    if len(matched_account_ids) == 1:
        return matched_account_ids[0]
    if len(matched_account_ids) > 1:
        raise ValueError("ambiguous_bank_account")
    return None


def _build_onboarding_reminder(global_state: dict[str, Any] | None) -> str | None:
    if not isinstance(global_state, dict) or global_state.get("mode") != "onboarding":
        return None

    substep = global_state.get("onboarding_substep")
    if substep == "profile_intro":
        return "(Pour continuer l’onboarding : clique sur « Allons-y ».)"
    if substep == "profile_collect":
        return "(Pour continuer l’onboarding : réponds aux informations demandées.)"
    if substep == "profile_confirm":
        return "(Pour continuer l’onboarding : réponds oui ou non pour confirmer le profil.)"
    if substep == "bank_accounts_collect":
        return "(Pour continuer l’onboarding : indique les banques à ajouter.)"
    if substep == "bank_accounts_confirm":
        return "(Pour continuer l’onboarding : réponds oui ou non à la question sur les comptes.)"
    if substep == "import_select_account":
        return "(Pour continuer : indique le compte à importer.)"
    if substep == "import_wait_ready":
        return "(Pour continuer : dis-moi quand ton fichier est prêt pour l’import.)"
    if substep == "categories_intro":
        return "(Pour continuer l’onboarding : démarrons le bootstrap des catégories.)"
    if substep == "categories_bootstrap":
        return "(Pour continuer l’onboarding : je prépare automatiquement les catégories et les marchands.)"
    if substep == "report_offer":
        return "(Pour continuer l’onboarding : réponds oui ou non pour ouvrir le rapport PDF.)"
    return None


def _missing_profile_field_question(profile_fields: dict[str, Any]) -> str:
    first_name = str(profile_fields.get("first_name") or "").strip()
    has_first_name = _is_profile_field_completed(first_name)
    has_last_name = _is_profile_field_completed(profile_fields.get("last_name"))
    has_birth_date = _is_birth_date_field_completed(profile_fields.get("birth_date"))

    if not has_first_name and not has_last_name:
        return "Quel est ton prénom et ton nom ?"
    if not has_first_name:
        return "Quel est ton prénom ?"
    if not has_last_name:
        return f"Ok {first_name} 🙂 Et ton nom de famille ?"
    if not has_birth_date:
        return f"Merci {first_name} 🙂\n\nQuelle est ta date de naissance ?"
    return ""


def _next_missing_profile_field(profile_fields: dict[str, Any]) -> str | None:
    """Return the next missing onboarding profile field in priority order."""

    if not _is_profile_field_completed(profile_fields.get("first_name")):
        return "first_name"
    if not _is_profile_field_completed(profile_fields.get("last_name")):
        return "last_name"
    if not _is_birth_date_field_completed(profile_fields.get("birth_date")):
        return "birth_date"
    return None


def _is_meta_answer(message: str) -> bool:
    """Return whether message is likely a meta reply instead of field value."""

    normalized = str(message or "").strip()
    if not normalized:
        return False
    lowered = normalized.lower()
    if any(pattern.search(normalized) for pattern in _ONBOARDING_PROFILE_META_ANSWER_PATTERNS):
        return True
    tokens = [token for token in _tokenize_profile_name_fragment(normalized) if token]
    if ("!" in normalized or "?" in normalized) and len(tokens) <= 2:
        return True
    has_pronoun = any(pronoun in lowered.split() for pronoun in {"tu", "je"})
    has_meta_verb = any(verb in lowered for verb in _ONBOARDING_PROFILE_META_VERBS)
    return has_pronoun and has_meta_verb


def _extract_name_from_message(message: str) -> tuple[str, str] | None:
    match = _ONBOARDING_NAME_PATTERN.match(message)
    if not match:
        return None
    first_name, last_name = match.groups()
    return first_name, last_name


def _normalize_name_token(token: str) -> str:
    return token.strip(" '\"-_").strip()


def _tokenize_profile_name_fragment(fragment: str) -> list[str]:
    normalized_fragment = re.sub(r"[\U00010000-\U0010ffff]", " ", fragment)
    tokens = [_normalize_name_token(item) for item in _ONBOARDING_PROFILE_TOKEN_PATTERN.findall(normalized_fragment)]
    cleaned_tokens: list[str] = []
    for token in tokens:
        if not token:
            continue
        lowered = token.lower()
        if lowered in _ONBOARDING_PROFILE_STOP_WORDS:
            continue
        cleaned_tokens.append(token)
    return cleaned_tokens


def _is_plausible_last_name(value: str) -> bool:
    normalized = str(value or "").strip()
    if len(normalized) < 2:
        return False
    lowered = normalized.lower()
    if lowered in _ONBOARDING_PROFILE_TROLL_LAST_NAMES:
        return False
    if any(char.isdigit() for char in normalized):
        return False
    if not _ONBOARDING_LAST_NAME_ALLOWED_CHARS_PATTERN.fullmatch(normalized):
        return False
    return True


def _is_plausible_first_name(value: str) -> bool:
    normalized = str(value or "").strip()
    if len(normalized) < 2:
        return False
    lowered = normalized.lower()
    if lowered in _ONBOARDING_PROFILE_TROLL_FIRST_NAMES:
        return False
    if any(char.isdigit() for char in normalized):
        return False
    if not _ONBOARDING_FIRST_NAME_ALLOWED_CHARS_PATTERN.fullmatch(normalized):
        return False
    if len([token for token in normalized.split() if token.strip()]) > 2:
        return False
    return True


def _is_toxic_message(message: str) -> bool:
    normalized = str(message or "")
    return any(pattern.search(normalized) for pattern in _ONBOARDING_PROFILE_TOXIC_PATTERNS)


def _is_low_signal_message(message: str) -> bool:
    normalized = str(message or "").strip().lower()
    if len(normalized) < 3:
        return True
    tokens = _ONBOARDING_PROFILE_TOKEN_PATTERN.findall(normalized)
    if len(tokens) == 0:
        if any(char.isdigit() for char in normalized):
            return False
        return True
    low_signal_tokens = {"serieux", "sérieux", "quoi", "hein", "ok", "okay", "lol", "es", "e"}
    if len(tokens) == 1 and tokens[0].lower() in low_signal_tokens:
        return True
    if all(token.lower() in low_signal_tokens for token in tokens):
        return True
    return False


def _is_plausible_birth_date(value_iso: str) -> bool:
    if not isinstance(value_iso, str):
        return False
    try:
        parsed = datetime.strptime(value_iso.strip(), "%Y-%m-%d").date()
    except ValueError:
        return False

    today = date.today()
    if parsed > today:
        return False

    age = today.year - parsed.year - ((today.month, today.day) < (parsed.month, parsed.day))
    return age <= 110


def extract_profile_fields_from_message(message: str) -> dict[str, Any]:
    """Extract profile fields from a free-form onboarding message."""

    extracted: dict[str, Any] = {
        "first_name": None,
        "last_name": None,
        "birth_date": None,
        "confidence": 0.0,
        "reason": "no_match",
    }
    raw_message = str(message or "").strip()
    if not raw_message:
        return extracted

    birth_date = _extract_birth_date_from_text(raw_message) or _extract_birth_date_from_message(raw_message)
    if birth_date is not None:
        extracted["birth_date"] = birth_date
        extracted["confidence"] = 0.95
        extracted["reason"] = "birth_date_detected"

    explicit_last_name = False
    last_name_match = _ONBOARDING_PROFILE_LAST_NAME_HINT_PATTERN.search(raw_message)
    if last_name_match:
        last_tokens = _tokenize_profile_name_fragment(last_name_match.group(1))
        if last_tokens:
            extracted["last_name"] = " ".join(last_tokens[:3])
            extracted["confidence"] = max(float(extracted["confidence"]), 0.9)
            extracted["reason"] = "explicit_last_name"
            explicit_last_name = True

    first_name_match = _ONBOARDING_PROFILE_FIRST_NAME_HINT_PATTERN.search(raw_message)
    if first_name_match:
        first_tokens = _tokenize_profile_name_fragment(first_name_match.group(1))
        if first_tokens:
            extracted["first_name"] = first_tokens[0]
            if len(first_tokens) > 1 and not explicit_last_name:
                extracted["last_name"] = " ".join(first_tokens[1:])
            confidence = 0.85 if len(first_tokens) == 1 else 0.9
            extracted["confidence"] = max(float(extracted["confidence"]), confidence)
            extracted["reason"] = "explicit_first_name"

    typo_mappelle_match = _ONBOARDING_PROFILE_JE_MAPPELLE_TYPO_PATTERN.search(raw_message)
    if typo_mappelle_match and extracted["first_name"] is None:
        trailing_fragment = raw_message[typo_mappelle_match.end() :]
        typo_tokens = _tokenize_profile_name_fragment(trailing_fragment)
        if typo_tokens:
            extracted["first_name"] = typo_tokens[0]
            extracted["last_name"] = extracted["last_name"] if explicit_last_name else None
            extracted["confidence"] = max(float(extracted["confidence"]), 0.85)
            extracted["reason"] = "je_mappelle_typo"

    if extracted["first_name"] is None and extracted["last_name"] is None:
        extracted_name = _extract_name_from_text_prefix(raw_message) or _extract_name_from_message(raw_message)
        if extracted_name is not None:
            extracted["first_name"], extracted["last_name"] = extracted_name
            extracted["confidence"] = max(float(extracted["confidence"]), 0.9)
            extracted["reason"] = "full_name_pattern"

    normalized_message = raw_message.lower()
    contains_non_name_hint = any(keyword in normalized_message for keyword in _ONBOARDING_PROFILE_NON_NAME_HINTS)

    if extracted["first_name"] is None and extracted["last_name"] is None and not contains_non_name_hint:
        generic_tokens = _tokenize_profile_name_fragment(raw_message)
        if generic_tokens and generic_tokens[0].lower() in {"pernom", "pérnom", "prenon", "prénon"}:
            extracted["confidence"] = max(float(extracted["confidence"]), 0.1)
            extracted["reason"] = "generic_name_typo_blocked"
        elif generic_tokens and generic_tokens[0].lower() in {"m", "m'appel", "mappel", "mappelle", "appel"}:
            extracted["confidence"] = max(float(extracted["confidence"]), 0.1)
            extracted["reason"] = "generic_prefix_blocked"
        elif 2 <= len(generic_tokens) <= 3:
            extracted["first_name"] = generic_tokens[0]
            extracted["last_name"] = " ".join(generic_tokens[1:])
            extracted["confidence"] = max(float(extracted["confidence"]), 0.85)
            extracted["reason"] = "generic_multi_token_name"
        elif len(generic_tokens) == 1:
            single_token = generic_tokens[0]
            if single_token.lower() not in {"jai", "j", "pas"}:
                extracted["first_name"] = single_token
                extracted["confidence"] = max(float(extracted["confidence"]), 0.6)
                extracted["reason"] = "generic_single_token_name"

    return extracted


def _extract_profile_fields_with_llm(message: str) -> dict[str, Any] | None:
    if not _config.llm_enabled():
        return None

    api_key = _config.openai_api_key()
    if not api_key:
        return None

    from openai import OpenAI

    prompt = (
        "Extrait les informations de profil utilisateur depuis un message d'onboarding. "
        "Réponds strictement avec un objet JSON valide qui respecte le schéma donné.\n"
        "Schéma:\n"
        "{\n"
        '  "first_name": string|null,\n'
        '  "last_name": string|null,\n'
        '  "birth_date": string|null,\n'
        '  "confidence": number,\n'
        '  "needs_clarification": boolean,\n'
        '  "clarification_question": string|null\n'
        "}\n"
        "Règles: pas d'invention; si incertain, needs_clarification=true et une question brève utile. "
        "birth_date doit être normalisée en YYYY-MM-DD si possible.\n"
        f"Message utilisateur: {message}"
    )
    client = OpenAI(api_key=api_key, timeout=10.0)
    response = client.chat.completions.create(
        model=_config.llm_model(),
        temperature=0.1,
        messages=[
            {"role": "system", "content": "Tu réponds uniquement avec un JSON strict valide."},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content if response.choices else None
    if not content:
        return None
    raw_payload = json.loads(content)
    parsed = _ProfileFieldExtractionLlmResponse.model_validate(raw_payload)
    birth_date = parsed.birth_date
    if isinstance(birth_date, str) and birth_date.strip():
        parsed_birth_date = _extract_birth_date_from_message(birth_date.strip())
        birth_date = parsed_birth_date
    return {
        "first_name": parsed.first_name.strip() if isinstance(parsed.first_name, str) and parsed.first_name.strip() else None,
        "last_name": parsed.last_name.strip() if isinstance(parsed.last_name, str) and parsed.last_name.strip() else None,
        "birth_date": birth_date,
        "confidence": max(0.0, min(1.0, float(parsed.confidence))),
        "reason": "llm_fallback",
        "needs_clarification": parsed.needs_clarification,
        "clarification_question": parsed.clarification_question,
    }


def _apply_field_gating(extraction: dict[str, Any], expected_field: str | None, raw_message: str) -> dict[str, Any]:
    """Apply expected-field gating while allowing explicit multi-field inputs."""

    if expected_field is None:
        return extraction

    gated = dict(extraction)
    explicit_birth_date = _extract_birth_date_from_text(raw_message) is not None

    if expected_field == "first_name":
        first_name = gated.get("first_name")
        last_name = gated.get("last_name")
        has_clear_full_name = bool(
            isinstance(first_name, str)
            and first_name.strip()
            and isinstance(last_name, str)
            and last_name.strip()
            and (
                str(gated.get("reason") or "") in {"full_name_pattern", "generic_multi_token_name"}
                or _extract_name_from_text_prefix(raw_message) is not None
                or _extract_name_from_message(raw_message) is not None
            )
        )
        if not has_clear_full_name:
            gated["last_name"] = None

        if gated.get("birth_date") is not None and not (gated.get("last_name") or explicit_birth_date):
            gated["birth_date"] = None
        return gated

    if expected_field == "last_name":
        gated["first_name"] = None
        if not explicit_birth_date:
            gated["birth_date"] = None
        return gated

    if expected_field == "birth_date":
        gated["first_name"] = None
        gated["last_name"] = None
        return gated

    return gated


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


def _maybe_fix_year_typo(message: str) -> tuple[str, str] | None:
    match = _ONBOARDING_BIRTH_DATE_YEAR_TYPO_PATTERN.search(str(message or ""))
    if not match:
        return None

    day_raw, month_raw, year_raw = match.groups()
    if year_raw[0:2] not in {"19", "20"} or year_raw.count("0") < 2:
        return None

    candidate_year = year_raw.replace("0", "", 1)
    if not re.fullmatch(r"\d{4}", candidate_year):
        return None

    normalized_month = unicodedata.normalize("NFKD", month_raw.strip().lower()).encode("ascii", "ignore").decode("ascii")
    mapped_month = _FRENCH_MONTH_TO_NUMBER.get(normalized_month)
    if mapped_month is None:
        return None

    try:
        parsed = date(year=int(candidate_year), month=mapped_month, day=int(day_raw))
    except ValueError:
        return None

    iso_value = parsed.isoformat()
    if not _is_plausible_birth_date(iso_value):
        return None
    return iso_value, iso_value


def _build_onboarding_global_state(
    existing_global_state: dict[str, Any] | None,
    *,
    onboarding_step: str = "profile",
    onboarding_substep: str | None = "profile_intro",
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
    debug: dict[str, Any] | None = None


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


class ImportJobCreateResponse(BaseModel):
    """Response payload when creating an async import job."""

    job_id: UUID


class ImportJobStatusResponse(BaseModel):
    """Snapshot payload for async import job status."""

    job_id: UUID
    status: str
    error_message: str | None = None
    updated_at: datetime | None = None
    total_transactions: int | None = None
    processed_transactions: int | None = None
    total_llm_items: int | None = None
    processed_llm_items: int | None = None


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


class SharedExpenseSuggestionDismissPayload(BaseModel):
    """Payload for shared expense suggestion dismiss endpoint."""

    reason: str | None = None


class SharedExpenseSuggestionApplyPayload(BaseModel):
    """Payload for shared expense suggestion apply endpoint."""

    amount: str | None = None


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
def get_loop_registry():
    """Create and cache loop registry once per process."""

    return build_default_registry()


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


def _get_shared_expenses_repository_or_501() -> SupabaseSharedExpensesRepository:
    """Return shared-expenses repository when Supabase config is available."""

    repository = _try_get_shared_expenses_repository()
    if repository is None:
        raise HTTPException(status_code=501, detail="shared expenses disabled")
    return repository




def _try_get_share_rules_repository() -> ShareRulesRepository | None:
    """Return share-rules repository when Supabase config is available, else ``None``."""

    supabase_url = _config.supabase_url()
    supabase_key = _config.supabase_service_role_key()
    if not supabase_url or not supabase_key:
        return None

    client = SupabaseClient(
        settings=SupabaseSettings(
            url=supabase_url,
            service_role_key=supabase_key,
            anon_key=_config.supabase_anon_key(),
        )
    )
    return SupabaseShareRulesRepository(client=client)

def _try_get_shared_expenses_repository() -> SharedExpensesRepository | None:
    """Return shared-expenses repository when Supabase config is available, else ``None``."""

    supabase_url = _config.supabase_url()
    supabase_key = _config.supabase_service_role_key()
    if not supabase_url or not supabase_key:
        return None

    client = SupabaseClient(
        settings=SupabaseSettings(
            url=supabase_url,
            service_role_key=supabase_key,
            anon_key=_config.supabase_anon_key(),
        )
    )
    return SupabaseSharedExpensesRepository(client=client)


def _try_get_import_jobs_repository() -> SupabaseImportJobsRepository | None:
    """Return async-import repository when Supabase config is available."""

    supabase_url = _config.supabase_url()
    supabase_key = _config.supabase_service_role_key()
    if not supabase_url or not supabase_key:
        return None

    client = SupabaseClient(
        settings=SupabaseSettings(
            url=supabase_url,
            service_role_key=supabase_key,
            anon_key=_config.supabase_anon_key(),
        )
    )
    return SupabaseImportJobsRepository(client=client)


def _get_import_jobs_repository_or_501() -> SupabaseImportJobsRepository:
    repository = _try_get_import_jobs_repository()
    if repository is None:
        raise HTTPException(status_code=501, detail="imports jobs disabled")
    return repository


def _resolve_authenticated_profile(request: Request, authorization: str | None) -> tuple[UUID, UUID]:
    """Resolve authenticated user and linked profile from auth header or query access token."""

    _ = authorization

    try:
        token = extract_bearer_token(request)
    except UnauthorizedError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

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
    ensure_profile = getattr(profiles_repository, "ensure_profile_for_auth_user", None)
    if callable(ensure_profile):
        try:
            profile_id = ensure_profile(auth_user_id=auth_user_id, email=email)
        except RuntimeError as exc:
            logger.exception("ensure_profile_for_auth_user_failed auth_user_id=%s", auth_user_id)
            raise HTTPException(
                status_code=500,
                detail="Unable to initialize authenticated profile",
            ) from exc
        return auth_user_id, profile_id

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

    error_id = _new_error_id()

    logger.exception(
        "unhandled_exception",
        extra={"error_id": error_id, "path": request.url.path, "method": request.method},
    )

    content: dict[str, str] = {"detail": "Internal Server Error", "error_id": error_id}
    if _is_debug_request(request):
        content["exception_type"] = type(exc).__name__
        content["exception_message"] = str(exc)

    return JSONResponse(status_code=500, content=content, headers={"X-Error-Id": error_id})


@app.get("/health")
def health() -> dict[str, str]:
    """Healthcheck endpoint."""

    return {"status": "ok"}


@app.post("/agent/chat", response_model=ChatResponse)
def agent_chat(
    request: Request,
    payload: ChatRequest,
    authorization: str | None = Header(default=None),
    x_debug: str | None = Header(default=None),
) -> JSONResponse:
    """Handle a user chat message through the agent loop."""

    logger.info("agent_chat_received message_length=%s", len(payload.message))
    debug_enabled = _is_debug_request(request, x_debug)
    registry = get_loop_registry()
    state_dict: dict[str, Any] = {}
    global_state: dict[str, Any] | None = None

    def _chat_response(*, reply: str, tool_result: Any | None, plan: Any | None = None) -> JSONResponse:
        payload_dict: dict[str, Any] = {
            "reply": reply,
            "tool_result": tool_result,
            "plan": plan,
        }
        if debug_enabled:
            debug_loop = _compute_debug_loop(state_dict, global_state, registry)
            payload_dict["debug"] = {"loop": debug_loop}
        return JSONResponse(content=payload_dict)

    profile_id: UUID | None = None
    try:
        auth_user_id, profile_id = _resolve_authenticated_profile(request, authorization)
        profiles_repository = get_profiles_repository()

        chat_state = _normalize_chat_state(
            profiles_repository.get_chat_state(profile_id=profile_id, user_id=auth_user_id)
        )

        active_task = chat_state.get("active_task")
        state = chat_state.get("state")
        state_dict = dict(state) if isinstance(state, dict) else {}
        shared_expense_active_task = chat_state.get("active_task")
        account_link_active_task = chat_state.get("active_task")
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

        if (
            _is_valid_global_state(global_state)
            and global_state.get("mode") == "onboarding"
            and (
                (
                    global_state.get("onboarding_step") == "profile"
                    and global_state.get("onboarding_substep") in _PROFILE_FIX_SUBSTEPS
                )
                or (
                    global_state.get("onboarding_step") == "bank_accounts"
                    and isinstance(global_state.get("onboarding_substep"), str)
                    and _BANK_ACCOUNTS_FIX_SUBSTEP_PATTERN.match(str(global_state.get("onboarding_substep"))) is not None
                )
            )
            and (
                bool(state_dict.get("session_resume_pending"))
                or payload.request_greeting
            )
        ):
            current_step = str(global_state.get("onboarding_step"))
            target_substep = "profile_confirm" if current_step == "profile" else "bank_accounts_confirm"
            updated_global_state = _build_onboarding_global_state(global_state, onboarding_step=current_step, onboarding_substep=target_substep)
            if current_step == "profile":
                updated_global_state["profile_confirmed"] = False
            else:
                updated_global_state["bank_accounts_confirmed"] = False
            global_state = _normalize_onboarding_step_substep(updated_global_state)
            state_dict = dict(state_dict) if isinstance(state_dict, dict) else {}
            state_dict["global_state"] = global_state
            loop_state = state_dict.get("loop")
            if isinstance(loop_state, dict):
                loop_id = str(loop_state.get("loop_id") or "")
                if loop_id.startswith("onboarding.profile_fix") or loop_id.startswith("onboarding.bank_accounts_fix"):
                    state_dict.pop("loop", None)
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

            if _is_profile_complete(profile_fields):
                updated_global_state = _build_onboarding_global_state(
                    None,
                    onboarding_step="profile",
                    onboarding_substep="profile_confirm",
                )
                updated_global_state["profile_confirmed"] = False
                state_dict["global_state"] = _normalize_onboarding_step_substep(updated_global_state)
                updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                updated_chat_state["state"] = state_dict
                profiles_repository.update_chat_state(
                    profile_id=profile_id,
                    user_id=auth_user_id,
                    chat_state=updated_chat_state,
                )
                return _chat_response(
                    reply=_build_profile_recap_reply(profile_fields),
                    tool_result=_build_quick_reply_yes_no_ui_action(),
                    plan=None,
                )

        should_offer_session_resume = (
            _is_valid_global_state(global_state)
            and global_state.get("mode") == "onboarding"
            and isinstance(global_state.get("onboarding_substep"), str)
            and global_state.get("onboarding_substep") in {"profile_collect", "profile_confirm", "bank_accounts_collect", "bank_accounts_confirm"}
        )
        session_resume_pending = bool(state_dict.get("session_resume_pending")) if isinstance(state_dict, dict) else False

        if session_resume_pending:
            if _is_allons_y(payload.message):
                state_dict = dict(state_dict) if isinstance(state_dict, dict) else {}
                state_dict["session_resume_pending"] = False
                updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                updated_chat_state["state"] = state_dict
                profiles_repository.update_chat_state(
                    profile_id=profile_id,
                    user_id=auth_user_id,
                    chat_state=updated_chat_state,
                )
                if _is_valid_global_state(global_state) and global_state.get("mode") == "onboarding":
                    if (
                        global_state.get("onboarding_step") == "profile"
                        and global_state.get("onboarding_substep") == "profile_confirm"
                        and hasattr(profiles_repository, "get_profile_fields")
                    ):
                        profile_fields = profiles_repository.get_profile_fields(
                            profile_id=profile_id,
                            fields=list(_PROFILE_COMPLETION_FIELDS),
                        )
                        if _is_profile_complete(profile_fields):
                            return _chat_response(
                                reply=_build_profile_recap_reply(profile_fields),
                                tool_result=_build_quick_reply_yes_no_ui_action(),
                                plan=None,
                            )
                    if global_state.get("onboarding_step") == "bank_accounts":
                        substep = global_state.get("onboarding_substep")
                        if hasattr(profiles_repository, "list_bank_accounts"):
                            existing_accounts = profiles_repository.list_bank_accounts(profile_id=profile_id)
                        else:
                            existing_accounts = []

                        if substep == "bank_accounts_confirm" and not existing_accounts:
                            substep = "bank_accounts_collect"
                            updated_global_state = _build_bank_accounts_onboarding_global_state(
                                global_state,
                                onboarding_substep="bank_accounts_collect",
                            )
                            updated_global_state["bank_accounts_confirmed"] = False
                            updated_global_state["has_bank_accounts"] = False
                            state_dict["global_state"] = updated_global_state
                            updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                            updated_chat_state["state"] = state_dict
                            profiles_repository.update_chat_state(
                                profile_id=profile_id,
                                user_id=auth_user_id,
                                chat_state=updated_chat_state,
                            )
                        if substep == "bank_accounts_collect":
                            return _chat_response(
                                reply=_BANK_ACCOUNTS_SELECTION_INTRO,
                                tool_result=_build_bank_accounts_form_ui_action(),
                                plan=None,
                            )
                        if substep == "bank_accounts_confirm":
                            return _chat_response(
                                reply=_build_bank_accounts_confirm_recap(existing_accounts),
                                tool_result=_build_quick_reply_yes_no_ui_action(),
                                plan=None,
                            )
                payload = payload.model_copy(update={"message": ""})
            else:
                return _chat_response(
                    reply=_ONBOARDING_SESSION_RESUME_REPLY,
                    tool_result=_build_onboarding_intro_quick_replies_ui_action(),
                    plan=None,
                )

        if payload.request_greeting and should_offer_session_resume and not session_resume_pending:
            state_dict = dict(state_dict) if isinstance(state_dict, dict) else {}
            state_dict["session_resume_pending"] = True
            updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
            updated_chat_state["state"] = state_dict
            profiles_repository.update_chat_state(
                profile_id=profile_id,
                user_id=auth_user_id,
                chat_state=updated_chat_state,
            )
            return _chat_response(
                reply=_ONBOARDING_SESSION_RESUME_REPLY,
                tool_result=_build_onboarding_intro_quick_replies_ui_action(),
                plan=None,
            )

        if (
            _is_valid_global_state(global_state)
            and global_state.get("mode") == "onboarding"
            and global_state.get("onboarding_step") == "profile"
            and global_state.get("onboarding_substep") == "profile_confirm"
            and hasattr(profiles_repository, "get_profile_fields")
        ):
            profile_fields = profiles_repository.get_profile_fields(
                profile_id=profile_id,
                fields=list(_PROFILE_COMPLETION_FIELDS),
            )
            if not _is_profile_complete(profile_fields):
                updated_global_state = _build_onboarding_global_state(
                    {
                        **global_state,
                        "profile_confirmed": False,
                    },
                    onboarding_step="profile",
                    onboarding_substep="profile_collect",
                )
                updated_global_state["profile_confirmed"] = False

                state_dict = dict(state_dict) if isinstance(state_dict, dict) else {}
                state_dict["global_state"] = _normalize_onboarding_step_substep(updated_global_state)
                loop_state = state_dict.get("loop")
                if isinstance(loop_state, dict) and loop_state.get("loop_id") == "onboarding.profile_confirm":
                    state_dict.pop("loop", None)

                updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                updated_chat_state["state"] = state_dict
                profiles_repository.update_chat_state(
                    profile_id=profile_id,
                    user_id=auth_user_id,
                    chat_state=updated_chat_state,
                )

                return _chat_response(
                    reply=_missing_profile_field_question(profile_fields) or "Quelle est ta date de naissance ?",
                    tool_result=_build_profile_collect_ui_action(profile_fields),
                    plan=None,
                )

        try:
            ui_form_submit_payload = _parse_ui_form_submit_message(payload.message)
        except (ValueError, json.JSONDecodeError):
            return _chat_response(reply="Soumission de formulaire invalide.", tool_result=None, plan=None)
        if ui_form_submit_payload is not None:
            form_id = ui_form_submit_payload.get("form_id")
            values = ui_form_submit_payload.get("values")
            if not isinstance(form_id, str) or not isinstance(values, dict):
                return _chat_response(reply="Soumission de formulaire invalide.", tool_result=None, plan=None)

            if form_id == "onboarding_profile_name":
                try:
                    first_name = _extract_required_form_value(values, "first_name")
                    last_name = _extract_required_form_value(values, "last_name")
                except ValueError:
                    return _chat_response(
                        reply="Merci de renseigner ton prénom et ton nom.",
                        tool_result=_build_profile_name_form_ui_action(),
                        plan=None,
                    )
                _update_profile_fields_safe(
                    profiles_repository=profiles_repository,
                    profile_id=profile_id,
                    user_id=auth_user_id,
                    set_dict={"first_name": first_name, "last_name": last_name},
                )
                refreshed_fields = profiles_repository.get_profile_fields(
                    profile_id=profile_id,
                    fields=list(_PROFILE_COMPLETION_FIELDS),
                )
                has_birth_date = _is_birth_date_field_completed(refreshed_fields.get("birth_date"))
                if not has_birth_date:
                    next_substep = "profile_collect"
                    reply_text = "Quelle est ta date de naissance ?"
                    tool_result = _build_profile_birth_date_form_ui_action(refreshed_fields)
                else:
                    next_substep = "profile_confirm"
                    reply_text = _build_profile_recap_reply(refreshed_fields)
                    tool_result = _build_quick_reply_yes_no_ui_action()

                updated_global_state = _build_onboarding_global_state(
                    global_state,
                    onboarding_step="profile",
                    onboarding_substep=next_substep,
                )
                updated_global_state["profile_confirmed"] = False
                state_dict = dict(state_dict) if isinstance(state_dict, dict) else {}
                state_dict["global_state"] = _normalize_onboarding_step_substep(updated_global_state)
                chat_state_to_save = dict(chat_state) if isinstance(chat_state, dict) else {}
                chat_state_to_save["state"] = state_dict
                profiles_repository.update_chat_state(
                    profile_id=profile_id,
                    user_id=auth_user_id,
                    chat_state=chat_state_to_save,
                )
                return _chat_response(reply=reply_text, tool_result=tool_result, plan=None)

            if form_id == "onboarding_profile_birth_date":
                try:
                    birth_date_raw = _extract_required_form_value(values, "birth_date")
                    birth_date = _extract_birth_date_from_message(birth_date_raw) or birth_date_raw
                    datetime.strptime(birth_date, "%Y-%m-%d")
                except ValueError:
                    return _chat_response(
                        reply="Merci de renseigner une date de naissance valide.",
                        tool_result=_build_profile_birth_date_form_ui_action(),
                        plan=None,
                    )
                if not _is_plausible_birth_date(birth_date):
                    return _chat_response(
                        reply="Cette date me paraît étrange. Peux-tu vérifier ?",
                        tool_result=_build_profile_birth_date_form_ui_action(
                            profiles_repository.get_profile_fields(
                                profile_id=profile_id,
                                fields=list(_PROFILE_COMPLETION_FIELDS),
                            )
                        ),
                        plan=None,
                    )
                _update_profile_fields_safe(
                    profiles_repository=profiles_repository,
                    profile_id=profile_id,
                    user_id=auth_user_id,
                    set_dict={"birth_date": birth_date},
                )
                refreshed_fields = profiles_repository.get_profile_fields(
                    profile_id=profile_id,
                    fields=list(_PROFILE_COMPLETION_FIELDS),
                )
                updated_global_state = _build_onboarding_global_state(
                    global_state,
                    onboarding_step="profile",
                    onboarding_substep="profile_confirm",
                )
                updated_global_state["profile_confirmed"] = False
                global_state = _normalize_onboarding_step_substep(updated_global_state)
                state_dict = dict(state_dict) if isinstance(state_dict, dict) else {}
                state_dict["global_state"] = global_state
                chat_state_to_save = dict(chat_state) if isinstance(chat_state, dict) else {}
                chat_state_to_save["state"] = state_dict
                profiles_repository.update_chat_state(
                    profile_id=profile_id,
                    user_id=auth_user_id,
                    chat_state=chat_state_to_save,
                )
                return _chat_response(
                    reply=_build_profile_recap_reply(refreshed_fields),
                    tool_result=_build_quick_reply_yes_no_ui_action(),
                    plan=None,
                )

            if form_id == "onboarding_bank_accounts":
                submitted_names = _extract_submitted_bank_account_names(values)
                if not submitted_names:
                    return _chat_response(
                        reply="Sélectionne au moins une banque pour continuer.",
                        tool_result=_build_bank_accounts_form_ui_action(),
                        plan=None,
                    )

                existing_accounts = profiles_repository.list_bank_accounts(profile_id=profile_id)
                existing_names_by_norm = {
                    _normalize_text(str(account.get("name") or "")): str(account.get("name") or "").strip()
                    for account in existing_accounts
                    if str(account.get("name") or "").strip()
                }
                desired_names_by_norm = {
                    _normalize_text(name): name
                    for name in submitted_names
                    if _normalize_text(name)
                }
                accounts_to_remove = [
                    existing_name
                    for norm_name, existing_name in existing_names_by_norm.items()
                    if norm_name not in desired_names_by_norm
                ]

                desired_names = list(desired_names_by_norm.values())
                if hasattr(profiles_repository, "sync_bank_accounts"):
                    profiles_repository.sync_bank_accounts(profile_id=profile_id, names=desired_names)
                else:
                    if accounts_to_remove and hasattr(profiles_repository, "remove_bank_accounts"):
                        profiles_repository.remove_bank_accounts(profile_id=profile_id, names=accounts_to_remove)
                    profiles_repository.ensure_bank_accounts(profile_id=profile_id, names=desired_names)
                refreshed_accounts = profiles_repository.list_bank_accounts(profile_id=profile_id)
                updated_global_state = _build_bank_accounts_onboarding_global_state(
                    global_state,
                    onboarding_substep="bank_accounts_confirm",
                )
                updated_global_state["bank_accounts_confirmed"] = False
                updated_global_state["has_bank_accounts"] = bool(refreshed_accounts)
                state_dict = dict(state_dict) if isinstance(state_dict, dict) else {}
                state_dict.pop("import_context", None)
                state_dict["global_state"] = updated_global_state
                chat_state_to_save = dict(chat_state) if isinstance(chat_state, dict) else {}
                chat_state_to_save["state"] = state_dict
                profiles_repository.update_chat_state(
                    profile_id=profile_id,
                    user_id=auth_user_id,
                    chat_state=chat_state_to_save,
                )
                return _chat_response(
                    reply=_build_bank_accounts_confirm_recap(refreshed_accounts),
                    tool_result=_build_quick_reply_yes_no_ui_action(),
                    plan=None,
                )

            return _chat_response(reply="Formulaire inconnu.", tool_result=None, plan=None)

        persisted_loop = parse_loop_context(state_dict.get("loop"))
        current_loop = persisted_loop
        used_implicit_loop = False
        if current_loop is None:
            current_loop = _build_implicit_loop_context(
                global_state=global_state,
                state_dict=state_dict,
                registry=registry,
            )
            used_implicit_loop = current_loop is not None

        onboarding_substep = global_state.get("onboarding_substep") if _is_valid_global_state(global_state) else None
        should_route_loop = current_loop is not None or (
            _is_valid_global_state(global_state)
            and global_state.get("mode") == "onboarding"
            and isinstance(onboarding_substep, str)
            and onboarding_substep in _ONBOARDING_ROUTEABLE_SUBSTEPS
        )
        if should_route_loop:
            tool_router = get_tool_router()
            loop_services = {
                "profiles_repository": profiles_repository,
                "tool_router": tool_router,
                "global_state": global_state or {},
                "state": state_dict,
            }
            loop_reply = route_message(
                message=payload.message,
                current_loop=current_loop,
                global_state=global_state or {},
                services=loop_services,
                profile_id=profile_id,
                user_id=auth_user_id,
                llm_judge=None,
                registry=registry,
            )

            resolved_loop = loop_reply.next_loop if loop_reply.handled else current_loop
            if used_implicit_loop and not loop_reply.handled:
                resolved_loop = None
            state_updates = loop_reply.updates if isinstance(loop_reply.updates, dict) else {}
            if used_implicit_loop and not loop_reply.handled and resolved_loop == current_loop and not state_updates:
                resolved_loop = None
            if resolved_loop is None:
                state_dict.pop("loop", None)
            else:
                state_dict["loop"] = serialize_loop_context(resolved_loop)

            if state_updates:
                if "global_state" in state_updates and _is_valid_global_state(state_updates["global_state"]):
                    global_state = _normalize_onboarding_step_substep(dict(state_updates["global_state"]))
                    state_dict["global_state"] = global_state
                for update_key, update_value in state_updates.items():
                    if update_key == "global_state":
                        continue
                    if update_value is None:
                        state_dict.pop(update_key, None)
                    else:
                        state_dict[update_key] = update_value
            resolved_global_state = state_dict.get("global_state") if isinstance(state_dict, dict) else None
            if (
                isinstance(resolved_global_state, dict)
                and resolved_global_state.get("mode") == "onboarding"
                and resolved_global_state.get("onboarding_step") == "profile"
                and resolved_global_state.get("onboarding_substep") != "profile_collect"
            ):
                loop_state = state_dict.get("loop") if isinstance(state_dict, dict) else None
                if isinstance(loop_state, dict) and loop_state.get("loop_id") == "onboarding.profile_collect":
                    state_dict.pop("loop", None)
                    resolved_loop = None

            should_persist_loop_state = (
                should_persist_global_state
                or bool(state_updates)
                or resolved_loop != persisted_loop
            )
            if should_persist_loop_state:
                updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                if state_dict:
                    updated_chat_state["state"] = state_dict
                else:
                    updated_chat_state.pop("state", None)
                profiles_repository.update_chat_state(
                    profile_id=profile_id,
                    user_id=auth_user_id,
                    chat_state=updated_chat_state,
                )

            if loop_reply.handled and loop_reply.reply.strip():
                tool_result = None
                lowered_reply = loop_reply.reply.lower()
                current_global_state = state_dict.get("global_state") if isinstance(state_dict, dict) else None
                if (
                    isinstance(current_global_state, dict)
                    and current_global_state.get("mode") == "onboarding"
                    and current_global_state.get("onboarding_step") == "profile"
                ):
                    current_substep = current_global_state.get("onboarding_substep")
                    if current_substep == "profile_confirm":
                        tool_result = _build_quick_reply_yes_no_ui_action()
                    elif current_substep == "profile_collect":
                        profile_fields = profiles_repository.get_profile_fields(
                            profile_id=profile_id,
                            fields=list(_PROFILE_COMPLETION_FIELDS),
                        )
                        tool_result = _build_profile_collect_ui_action(profile_fields)
                elif "peux-tu confirmer ton année de naissance" in lowered_reply:
                    tool_result = _build_quick_reply_yes_no_ui_action()
                return _chat_response(reply=loop_reply.reply, tool_result=tool_result, plan=None)

        import_context = state_dict.get("import_context") if isinstance(state_dict, dict) else None
        pending_files = import_context.get("pending_files") if isinstance(import_context, dict) else None
        if isinstance(pending_files, list) and pending_files and hasattr(profiles_repository, "list_bank_accounts"):
            existing_accounts = profiles_repository.list_bank_accounts(profile_id=profile_id)
            matched_account = _match_bank_account_name(payload.message, existing_accounts)
            if matched_account is not None:
                tool_payload = {
                    "files": pending_files,
                    "bank_account_id": str(matched_account.get("id")),
                    "import_mode": "commit",
                    "modified_action": "replace",
                }
                import_result = get_tool_router().call("finance_releves_import_files", tool_payload, profile_id=profile_id)
                if isinstance(import_result, ToolError):
                    return _chat_response(reply=f"Import impossible: {import_result.message}", tool_result=None, plan=None)

                updated_state = dict(state_dict) if isinstance(state_dict, dict) else {}
                updated_import_context = dict(import_context) if isinstance(import_context, dict) else {}
                updated_import_context.pop("pending_files", None)
                updated_import_context.pop("clarification_accounts", None)
                if updated_import_context:
                    updated_state["import_context"] = updated_import_context
                else:
                    updated_state.pop("import_context", None)

                updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                updated_chat_state["state"] = updated_state
                profiles_repository.update_chat_state(
                    profile_id=profile_id,
                    user_id=auth_user_id,
                    chat_state=updated_chat_state,
                )
                imported_count = int((import_result or {}).get("imported_count", 0)) if isinstance(import_result, dict) else 0
                account_name = str(matched_account.get("name") or "ce compte")
                return _chat_response(
                    reply=f"Parfait, j’ai importé le relevé sur {account_name}. {imported_count} transactions détectées.",
                    tool_result=None,
                    plan=None,
                )

        profile_complete = _has_complete_profile(profiles_repository, profile_id)
        current_mode = global_state.get("mode") if _is_valid_global_state(global_state) else None
        current_step = global_state.get("onboarding_step") if _is_valid_global_state(global_state) else None
        current_substep = global_state.get("onboarding_substep") if _is_valid_global_state(global_state) else None
        should_force_profile_re_gate = not (
            current_mode == "onboarding"
            and current_step == "profile"
            and current_substep in {"profile_intro", "profile_collect", "profile_confirm", "profile_fix_select", "profile_fix_name", "profile_fix_birth_date"}
        )
        if profile_complete is False and should_force_profile_re_gate:
            updated_global_state = _build_onboarding_global_state(
                global_state if _is_valid_global_state(global_state) else None,
                onboarding_step="profile",
                onboarding_substep="profile_intro",
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
            return _chat_response(
                reply=_ONBOARDING_PROFILE_INTRO_REPLY,
                tool_result=_build_onboarding_intro_quick_replies_ui_action(),
                plan=None,
            )

        is_onboarding_profile_intro = (
            _is_valid_global_state(global_state)
            and global_state.get("mode") == "onboarding"
            and global_state.get("onboarding_step") == "profile"
            and global_state.get("onboarding_substep") == "profile_intro"
        )
        if payload.request_greeting and is_onboarding_profile_intro:
            return _chat_response(
                reply=_ONBOARDING_PROFILE_INTRO_REPLY,
                tool_result=_build_onboarding_intro_quick_replies_ui_action(),
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
                    substep = "profile_confirm" if _is_profile_complete(profile_fields) else "profile_intro"

                if _is_profile_complete(profile_fields) and substep in {"profile_intro", "profile_collect"}:
                    updated_global_state = _build_onboarding_global_state(
                        global_state,
                        onboarding_step="profile",
                        onboarding_substep="profile_confirm",
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
                    return _chat_response(
                        reply=_build_profile_recap_reply(profile_fields),
                        tool_result=_build_quick_reply_yes_no_ui_action(),
                        plan=None,
                    )

                if substep == "profile_intro":
                    normalized_message = _normalize_text(payload.message)
                    if _is_allons_y(payload.message):
                        updated_global_state = _build_onboarding_global_state(
                            global_state,
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
                        return _chat_response(
                            reply="Renseigne ton prénom et ton nom.",
                            tool_result=_build_profile_collect_ui_action(profile_fields),
                            plan=None,
                        )
                    state_dict["global_state"] = global_state
                    updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                    updated_chat_state["state"] = state_dict
                    profiles_repository.update_chat_state(
                        profile_id=profile_id,
                        user_id=auth_user_id,
                        chat_state=updated_chat_state,
                    )
                    return _chat_response(
                        reply=_ONBOARDING_PROFILE_INTRO_REPLY,
                        tool_result=_build_onboarding_intro_quick_replies_ui_action(),
                        plan=None,
                    )

                if substep == "profile_collect":
                    has_name = _is_profile_field_completed(profile_fields.get("first_name")) and _is_profile_field_completed(
                        profile_fields.get("last_name")
                    )
                    has_birth_date = _is_birth_date_field_completed(profile_fields.get("birth_date"))

                    if has_name and has_birth_date:
                        updated_global_state = _build_onboarding_global_state(
                            global_state,
                            onboarding_step="profile",
                            onboarding_substep="profile_confirm",
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
                        return _chat_response(
                            reply=_build_profile_recap_reply(profile_fields),
                            tool_result=_build_quick_reply_yes_no_ui_action(),
                            plan=None,
                        )

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
                    return _chat_response(
                        reply=(
                            "Renseigne ton prénom et ton nom."
                            if not has_name
                            else "Quelle est ta date de naissance ?"
                        ),
                        tool_result=_build_profile_collect_ui_action(profile_fields),
                        plan=None,
                    )

                if substep == "profile_fix_select":
                    normalized_correction = _normalize_text(payload.message)
                    if normalized_correction in {"corriger_nom", "name"}:
                        updated_global_state = _build_onboarding_global_state(
                            {
                                **global_state,
                                "profile_confirmed": False,
                            },
                            onboarding_step="profile",
                            onboarding_substep="profile_fix_name",
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
                        return _chat_response(
                            reply="Renseigne ton prénom et ton nom.",
                            tool_result=_build_profile_name_form_ui_action(profile_fields),
                            plan=None,
                        )
                    if normalized_correction in {"corriger_date", "birth_date"}:
                        updated_global_state = _build_onboarding_global_state(
                            {
                                **global_state,
                                "profile_confirmed": False,
                            },
                            onboarding_step="profile",
                            onboarding_substep="profile_fix_birth_date",
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
                        return _chat_response(
                            reply="Quelle est ta date de naissance ?",
                            tool_result=_build_profile_birth_date_form_ui_action(profile_fields),
                            plan=None,
                        )
                    return _chat_response(
                        reply="Pas de souci 🙂 Qu’est-ce que tu veux corriger ?",
                        tool_result=_build_quick_reply_profile_fix_ui_action(),
                        plan=None,
                    )

                if substep in {"profile_fix_name", "profile_fix_birth_date"}:
                    if substep == "profile_fix_name":
                        return _chat_response(
                            reply="Renseigne ton prénom et ton nom.",
                            tool_result=_build_profile_name_form_ui_action(profile_fields),
                            plan=None,
                        )
                    return _chat_response(
                        reply="Quelle est ta date de naissance ?",
                        tool_result=_build_profile_birth_date_form_ui_action(profile_fields),
                        plan=None,
                    )

                if substep == "profile_confirm":
                    if _is_yes(payload.message):
                        state_dict.pop("profile_correction_choice_pending", None)
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
                        return _chat_response(
                            reply=_BANK_ACCOUNTS_SELECTION_INTRO,
                            tool_result=_build_bank_accounts_form_ui_action(),
                            plan=None,
                        )
                    if _is_no(payload.message):
                        state_dict.pop("profile_correction_choice_pending", None)
                        updated_global_state = _build_onboarding_global_state(
                            {
                                **global_state,
                                "profile_confirmed": False,
                            },
                            onboarding_step="profile",
                            onboarding_substep="profile_fix_select",
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
                        return _chat_response(
                            reply="Pas de souci 🙂 Qu’est-ce que tu veux corriger ?",
                            tool_result=_build_quick_reply_profile_fix_ui_action(),
                            plan=None,
                        )
                    return _chat_response(reply="Est-ce bien correct ?", tool_result=_build_quick_reply_yes_no_ui_action(), plan=None)


            mode = global_state.get("mode")
            onboarding_step = global_state.get("onboarding_step")

            if mode == "onboarding" and onboarding_step == "import" and global_state.get("onboarding_substep") == "import_wait_ready":
                if _is_yes(payload.message):
                    import_context = state_dict.get("import_context") if isinstance(state_dict.get("import_context"), dict) else {}
                    selected_bank_account_id = None
                    if isinstance(import_context, dict):
                        selected_bank_account_id = import_context.get("bank_account_id") or import_context.get("selected_bank_account_id")

                    if (not selected_bank_account_id) and hasattr(profiles_repository, "list_bank_accounts"):
                        existing_accounts = profiles_repository.list_bank_accounts(profile_id=profile_id)
                        if len(existing_accounts) == 1:
                            selected_account = existing_accounts[0]
                            selected_bank_account_id = str(selected_account.get("id") or "").strip() or None
                            account_name = str(selected_account.get("name") or "").strip()
                            state_dict["import_context"] = {
                                "bank_account_id": selected_bank_account_id,
                                "selected_bank_account_id": selected_bank_account_id,
                                "selected_bank_account_name": account_name,
                            }
                            updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                            updated_chat_state["state"] = state_dict
                            profiles_repository.update_chat_state(
                                profile_id=profile_id,
                                user_id=auth_user_id,
                                chat_state=updated_chat_state,
                            )
                    return _chat_response(
                        reply="Parfait 🙂\n\nClique sur « Importer maintenant » pour sélectionner ton fichier CSV.",
                        tool_result=_build_import_file_ui_request(state_dict.get("import_context")),
                        plan=None,
                    )
                return _chat_response(
                    reply=_IMPORT_WAIT_READY_REPLY,
                    tool_result=_build_quick_reply_yes_no_ui_action(),
                    plan=None,
                )

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
                return _chat_response(
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
                return _chat_response(
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
                    onboarding_substep="import_wait_ready",
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
                return _chat_response(
                    reply=_IMPORT_WAIT_READY_REPLY,
                    tool_result=_build_quick_reply_yes_no_ui_action(),
                    plan=None,
                )

            if mode == "onboarding" and onboarding_step == "bank_accounts" and hasattr(profiles_repository, "list_bank_accounts") and hasattr(profiles_repository, "ensure_bank_accounts"):
                substep = global_state.get("onboarding_substep") or "bank_accounts_collect"
                existing_accounts = profiles_repository.list_bank_accounts(profile_id=profile_id)

                if substep == "bank_accounts_collect":
                    if existing_accounts and not bool(global_state.get("bank_accounts_confirmed", False)):
                        accounts_display = _format_accounts_for_reply(existing_accounts)
                        updated_global_state = _build_bank_accounts_onboarding_global_state(
                            global_state,
                            onboarding_substep="bank_accounts_confirm",
                        )
                        updated_global_state["has_bank_accounts"] = True
                        updated_global_state["bank_accounts_confirmed"] = False
                        state_dict["global_state"] = updated_global_state
                        updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                        updated_chat_state["state"] = state_dict
                        profiles_repository.update_chat_state(
                            profile_id=profile_id,
                            user_id=auth_user_id,
                            chat_state=updated_chat_state,
                        )
                        return _chat_response(
                            reply=_build_bank_accounts_confirm_recap(existing_accounts),
                            tool_result=_build_quick_reply_yes_no_ui_action(),
                            plan=None,
                        )

                    return _chat_response(
                        reply=_BANK_ACCOUNTS_SELECTION_INTRO,
                        tool_result=_build_bank_accounts_form_ui_action(),
                        plan=None,
                    )

                    if _is_no(payload.message):
                        if existing_accounts:
                            accounts_display = _format_accounts_for_reply(existing_accounts)
                            updated_global_state = _build_bank_accounts_onboarding_global_state(
                                global_state,
                                onboarding_substep="bank_accounts_confirm",
                            )
                            updated_global_state["has_bank_accounts"] = True
                            updated_global_state["bank_accounts_confirmed"] = False
                            state_dict["global_state"] = updated_global_state
                            updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                            updated_chat_state["state"] = state_dict
                            profiles_repository.update_chat_state(
                                profile_id=profile_id,
                                user_id=auth_user_id,
                                chat_state=updated_chat_state,
                            )
                            return _chat_response(
                                reply=_build_bank_accounts_confirm_recap(existing_accounts),
                                tool_result=_build_quick_reply_yes_no_ui_action(),
                                plan=None,
                            )
                        return _chat_response(
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
                        updated_global_state["bank_accounts_confirmed"] = False
                        state_dict["global_state"] = updated_global_state
                        updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                        updated_chat_state["state"] = state_dict
                        profiles_repository.update_chat_state(
                            profile_id=profile_id,
                            user_id=auth_user_id,
                            chat_state=updated_chat_state,
                        )
                        return _chat_response(
                            reply=_build_bank_accounts_confirm_recap(existing_accounts),
                            tool_result=_build_quick_reply_yes_no_ui_action(),
                            plan=None,
                        )

                    matched_banks, unknown_segments = extract_canonical_banks(payload.message)
                    if not matched_banks:
                        normalized_message = payload.message.lower()
                        message_looks_like_request = any(hint in normalized_message for hint in _BANK_ACCOUNTS_REQUEST_HINTS)
                        if message_looks_like_request:
                            return _chat_response(
                                reply="Avant de continuer, indique-moi tes banques (ex: UBS, Revolut).",
                                tool_result=None,
                                plan=None,
                            )
                        if unknown_segments:
                            return _chat_response(
                                reply=(
                                    f"Je n’ai pas reconnu: {', '.join(unknown_segments)}. "
                                    "Peux-tu donner le nom exact de ta banque ?"
                                ),
                                tool_result=None,
                                plan=None,
                            )
                        return _chat_response(
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
                    updated_global_state["bank_accounts_confirmed"] = False
                    updated_global_state["has_bank_accounts"] = bool(refreshed_accounts)
                    state_dict.pop("import_context", None)

                    state_dict["global_state"] = updated_global_state
                    updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                    updated_chat_state["state"] = state_dict
                    profiles_repository.update_chat_state(
                        profile_id=profile_id,
                        user_id=auth_user_id,
                        chat_state=updated_chat_state,
                    )

                    return _chat_response(
                        reply=_build_bank_accounts_confirm_recap(refreshed_accounts),
                        tool_result=_build_quick_reply_yes_no_ui_action(),
                        plan=None,
                    )

                if substep == "bank_accounts_confirm":
                    if not existing_accounts:
                        updated_global_state = _build_bank_accounts_onboarding_global_state(
                            global_state,
                            onboarding_substep="bank_accounts_collect",
                        )
                        updated_global_state["bank_accounts_confirmed"] = False
                        updated_global_state["has_bank_accounts"] = False
                        state_dict["global_state"] = updated_global_state
                        updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                        updated_chat_state["state"] = state_dict
                        profiles_repository.update_chat_state(
                            profile_id=profile_id,
                            user_id=auth_user_id,
                            chat_state=updated_chat_state,
                        )
                        return _chat_response(
                            reply=_BANK_ACCOUNTS_SELECTION_INTRO,
                            tool_result=_build_bank_accounts_form_ui_action(),
                            plan=None,
                        )
                    if _is_no(payload.message):
                        updated_global_state = _build_bank_accounts_onboarding_global_state(
                            global_state,
                            onboarding_substep="bank_accounts_collect",
                        )
                        updated_global_state["bank_accounts_confirmed"] = False
                        updated_global_state["has_bank_accounts"] = bool(existing_accounts)
                        state_dict["global_state"] = updated_global_state
                        updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                        updated_chat_state["state"] = state_dict
                        profiles_repository.update_chat_state(
                            profile_id=profile_id,
                            user_id=auth_user_id,
                            chat_state=updated_chat_state,
                        )
                        return _chat_response(
                            reply="Ok 🙂 Modifie ta sélection.",
                            tool_result=_build_bank_accounts_form_ui_action(),
                            plan=None,
                        )
                    if _is_yes(payload.message):
                        if not existing_accounts:
                            return _chat_response(
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
                            onboarding_substep="import_wait_ready",
                        )
                        updated_global_state["bank_accounts_confirmed"] = True
                        updated_global_state["has_bank_accounts"] = bool(existing_accounts)
                        state_dict["global_state"] = updated_global_state
                        state_dict.pop("import_context", None)
                        updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                        updated_chat_state["state"] = state_dict

                        if len(existing_accounts) == 1:
                            selected_account = existing_accounts[0]
                            state_dict["import_context"] = {
                                "bank_account_id": str(selected_account.get("id")),
                                "selected_bank_account_id": str(selected_account.get("id")),
                                "selected_bank_account_name": str(selected_account.get("name", "")),
                            }
                            updated_chat_state["state"] = state_dict
                            profiles_repository.update_chat_state(
                                profile_id=profile_id,
                                user_id=auth_user_id,
                                chat_state=updated_chat_state,
                            )
                            return _chat_response(
                                reply=_IMPORT_WAIT_READY_REPLY,
                                tool_result=_build_quick_reply_yes_no_ui_action(),
                                plan=None,
                            )

                        profiles_repository.update_chat_state(
                            profile_id=profile_id,
                            user_id=auth_user_id,
                            chat_state=updated_chat_state,
                        )
                        return _chat_response(
                            reply=_IMPORT_WAIT_READY_REPLY,
                            tool_result=_build_quick_reply_yes_no_ui_action(),
                            plan=None,
                        )
                    return _chat_response(reply=_build_bank_accounts_confirm_recap(existing_accounts), tool_result=_build_quick_reply_yes_no_ui_action(), plan=None)

            if mode == "onboarding" and onboarding_step == "import" and global_state.get("onboarding_substep") == "import_select_account" and hasattr(profiles_repository, "list_bank_accounts"):
                existing_accounts = profiles_repository.list_bank_accounts(profile_id=profile_id)
                matched_account = _match_bank_account_name(payload.message, existing_accounts)
                if matched_account is None:
                    return _chat_response(
                        reply=(
                            "Je ne trouve pas ce compte. Comptes dispo: "
                            f"{_format_accounts_for_reply(existing_accounts)}"
                        ),
                        tool_result=None,
                        plan=None,
                    )

                updated_state = dict(state_dict)
                updated_state["import_context"] = {
                    "bank_account_id": str(matched_account.get("id")),
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
                return _chat_response(
                    reply=f"On peut importer ton premier relevé maintenant.\n\nEnvoie-moi le fichier CSV de ton compte {account_name}.",
                    tool_result=ui_request,
                    plan=None,
                )

            if mode == "onboarding" and onboarding_step == "categories":
                substep = global_state.get("onboarding_substep")
                if substep in {"categories_intro", "categories_bootstrap"}:
                    profiles_repository.ensure_system_categories(
                        profile_id=profile_id,
                        categories=_build_system_categories_payload(),
                    )
                    _ = profiles_repository.list_merchants_without_category(profile_id=profile_id)
                    _classify_merchants_without_category(
                        profiles_repository=profiles_repository,
                        profile_id=profile_id,
                    )

                    updated_global_state = _build_onboarding_global_state(
                        global_state,
                        onboarding_step="report",
                        onboarding_substep="report_offer",
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

                    return _chat_response(
                        reply=_build_import_done_reply(None),
                        tool_result=_build_quick_reply_yes_no_ui_action(),
                        plan=None,
                    )


            if mode == "onboarding" and onboarding_step == "report" and global_state.get("onboarding_substep") == "report_offer":
                if _is_yes(payload.message):
                    month_value: str | None = None
                    start_date_value: str | None = None
                    end_date_value: str | None = None
                    bank_account_id_value: str | None = None

                    last_query = state_dict.get("last_query") if isinstance(state_dict, dict) else None
                    if isinstance(last_query, dict):
                        if isinstance(last_query.get("month"), str) and str(last_query.get("month")).strip():
                            month_value = str(last_query.get("month")).strip()

                    if month_value is None:
                        start_date_value, end_date_value, bank_account_id_value = _resolve_last_report_filters(state_dict)

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
                        bank_account_id=bank_account_id_value,
                    )
                    updated_global_state = _build_onboarding_global_state(
                        global_state,
                        onboarding_step="report",
                        onboarding_substep="report_sent",
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
                    return _chat_response(
                        reply="Voici ton premier rapport financier !",
                        tool_result=_build_open_pdf_ui_request(report_url),
                        plan=None,
                    )
                if _is_no(payload.message):
                    return _chat_response(
                        reply="Ok 🙂 Dis-moi quand tu veux le voir.",
                        tool_result=None,
                        plan=None,
                    )
                return _chat_response(reply="Réponds par oui ou non.", tool_result=None, plan=None)

        if _should_prompt_household_link_setup(
            global_state=global_state,
            profiles_repository=profiles_repository,
            profile_id=profile_id,
        ):
            next_state = dict(state_dict) if isinstance(state_dict, dict) else {}
            next_global_state = dict(global_state) if isinstance(global_state, dict) else {}
            next_global_state["household_link_prompted"] = True
            next_state["global_state"] = _normalize_onboarding_step_substep(next_global_state)
            updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
            updated_chat_state["state"] = next_state
            updated_chat_state["active_task"] = {
                "type": "account_link_setup",
                "step": "ask_has_shared_expenses",
                "draft": {},
            }
            profiles_repository.update_chat_state(
                profile_id=profile_id,
                user_id=auth_user_id,
                chat_state=updated_chat_state,
            )
            return _chat_response(
                reply=_HOUSEHOLD_LINK_AUTO_PROMPT_REPLY,
                tool_result=None,
                plan=None,
            )

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
                    return _chat_response(
                        reply=pending_reply,
                        tool_result=serialized_pending_result,
                        plan=jsonable_encoder({"tool_name": tool_name, "payload": tool_payload}),
                    )

        if isinstance(account_link_active_task, dict) and account_link_active_task.get("type") == "account_link_setup":
            reply_text, updated_link_task, updated_state_dict, link_tool_result = _execute_account_link_setup_task(
                user_message=payload.message,
                active_task=account_link_active_task,
                state_dict=state_dict if isinstance(state_dict, dict) else None,
                profile_id=profile_id,
                profiles_repository=profiles_repository,
                debug_enabled=debug_enabled,
            )
            updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
            if updated_link_task is None:
                updated_chat_state.pop("active_task", None)
            else:
                updated_chat_state["active_task"] = updated_link_task
            if isinstance(updated_state_dict, dict):
                updated_chat_state["state"] = updated_state_dict
            profiles_repository.update_chat_state(
                profile_id=profile_id,
                user_id=auth_user_id,
                chat_state=updated_chat_state,
            )
            return _chat_response(reply=reply_text, tool_result=link_tool_result, plan=None)

        if _is_account_link_setup_intent(payload.message):
            existing_link = None
            if hasattr(profiles_repository, "get_active_household_link"):
                try:
                    existing_link = profiles_repository.get_active_household_link(profile_id=profile_id)
                except Exception:
                    logger.exception("account_link_fetch_failed profile_id=%s", profile_id)
            seed_state = state_dict if isinstance(state_dict, dict) else {}
            if isinstance(existing_link, dict):
                next_state = dict(seed_state)
                global_state_entry = next_state.get("global_state") if isinstance(next_state.get("global_state"), dict) else {}
                global_state_entry = dict(global_state_entry)
                global_state_entry["household_link"] = existing_link
                next_state["global_state"] = global_state_entry
                seed_state = next_state

            reply_text, updated_link_task, updated_state_dict, link_tool_result = _execute_account_link_setup_task(
                user_message="oui",
                active_task=None,
                state_dict=seed_state,
                profile_id=profile_id,
                profiles_repository=profiles_repository,
                debug_enabled=debug_enabled,
            )
            updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
            if updated_link_task is None:
                updated_chat_state.pop("active_task", None)
            else:
                updated_chat_state["active_task"] = updated_link_task
            if isinstance(updated_state_dict, dict):
                updated_chat_state["state"] = updated_state_dict
            profiles_repository.update_chat_state(
                profile_id=profile_id,
                user_id=auth_user_id,
                chat_state=updated_chat_state,
            )
            return _chat_response(reply=reply_text, tool_result=link_tool_result, plan=None)

        share_rule_command = parse_share_rule_command(payload.message)
        if isinstance(share_rule_command, dict):
            error_code = share_rule_command.get("error")
            if error_code == "category_unknown":
                return _chat_response(
                    reply=(
                        "Je n’ai pas reconnu la catégorie. Catégories possibles: "
                        "logement, alimentation, assurance, abonnements, transport, loisirs, habits, cadeaux."
                    ),
                    tool_result=None,
                    plan=None,
                )
            if error_code == "invalid_boost":
                return _chat_response(
                    reply="Je n’ai pas compris le boost. Utilise par exemple: ‘boost logement +0.2’ (valeur > 0 et <= 1).",
                    tool_result=None,
                    plan=None,
                )

            share_rules_repository = _try_get_share_rules_repository()
            if share_rules_repository is None:
                return _chat_response(reply="Fonction indisponible (share rules disabled)", tool_result=None, plan=None)

            share_rules_repository.upsert_share_rule(
                profile_id=profile_id,
                rule_type=str(share_rule_command["rule_type"]),
                rule_key=str(share_rule_command["rule_key"]),
                action=str(share_rule_command["action"]),
                boost_value=share_rule_command.get("boost_value"),
            )

            requested_category = str(share_rule_command["rule_key"])
            display_category = next(
                (
                    alias
                    for alias, category_norm in _SHARE_RULE_CATEGORY_ALIASES.items()
                    if category_norm == requested_category and alias in {"logement", "alimentation", "assurance", "abonnements", "transport", "loisirs", "habits", "cadeaux"}
                ),
                requested_category,
            )
            action = str(share_rule_command["action"])
            if action == "force_share":
                confirmation = f"Règle enregistrée ✅ : {display_category} → toujours partagé (score forcé à 1)."
                undo_hint = f"Pour annuler: ‘ne jamais partager {display_category}’."
            elif action == "force_exclude":
                confirmation = f"Règle enregistrée ✅ : {display_category} → ne jamais partager (score forcé à 0)."
                undo_hint = f"Pour annuler: ‘partage {display_category}’."
            else:
                boost_value = share_rule_command.get("boost_value")
                confirmation = f"Règle enregistrée ✅ : {display_category} → boost +{boost_value}."
                undo_hint = f"Pour annuler: ‘ne jamais partager {display_category}’ ou ‘partage {display_category}’."

            link_state = None
            state_global = state_dict.get("global_state") if isinstance(state_dict, dict) else None
            if isinstance(state_global, dict):
                household_link = state_global.get("household_link")
                if isinstance(household_link, dict):
                    link_state = household_link
            if link_state is None and hasattr(profiles_repository, "get_active_household_link"):
                try:
                    maybe_link = profiles_repository.get_active_household_link(profile_id=profile_id)
                except Exception:
                    logger.exception("share_rule_chat_get_active_household_link_failed profile_id=%s", profile_id)
                    maybe_link = None
                if isinstance(maybe_link, dict):
                    link_state = maybe_link

            if not isinstance(link_state, dict):
                return _chat_response(
                    reply=f"{confirmation}\n{undo_hint}\nDis ‘valider partage’ pour voir les nouvelles suggestions.",
                    tool_result=None,
                    plan=None,
                )

            try:
                _seed_shared_expense_suggestions_after_link_setup(profile_id=profile_id, link_state=link_state)
                validation_reply, validation_task = handle_shared_expenses_validation_request(profile_id=profile_id)
            except HTTPException:
                return _chat_response(
                    reply=f"{confirmation}\n{undo_hint}\nDis ‘valider partage’ pour voir les nouvelles suggestions.",
                    tool_result=None,
                    plan=None,
                )

            updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
            if validation_task is None:
                updated_chat_state.pop("active_task", None)
            else:
                updated_chat_state["active_task"] = validation_task
            profiles_repository.update_chat_state(
                profile_id=profile_id,
                user_id=auth_user_id,
                chat_state=updated_chat_state,
            )
            if validation_task is None:
                return _chat_response(
                    reply=f"{confirmation}\n{undo_hint}\nDis ‘valider partage’ pour voir les nouvelles suggestions.",
                    tool_result=None,
                    plan=None,
                )
            return _chat_response(reply=f"{confirmation}\n{undo_hint}\n\n{validation_reply}", tool_result=None, plan=None)

        if isinstance(shared_expense_active_task, dict) and shared_expense_active_task.get("type") == "shared_expense_confirm":
            reply_text, updated_shared_task = _execute_shared_expense_confirmation_actions(
                profile_id=profile_id,
                active_task=shared_expense_active_task,
                user_message=payload.message,
            )
            updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
            if updated_shared_task is None:
                updated_chat_state.pop("active_task", None)
            else:
                updated_chat_state["active_task"] = updated_shared_task
            profiles_repository.update_chat_state(
                profile_id=profile_id,
                user_id=auth_user_id,
                chat_state=updated_chat_state,
            )
            return _chat_response(reply=reply_text, tool_result=None, plan=None)

        if _is_shared_expense_validation_intent(payload.message):
            reply_text, updated_shared_task = handle_shared_expenses_validation_request(profile_id=profile_id)
            updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
            if updated_shared_task is None:
                updated_chat_state.pop("active_task", None)
            else:
                updated_chat_state["active_task"] = updated_shared_task
            profiles_repository.update_chat_state(
                profile_id=profile_id,
                user_id=auth_user_id,
                chat_state=updated_chat_state,
            )
            return _chat_response(reply=reply_text, tool_result=None, plan=None)

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
            return _chat_response(
                reply=f"Voici ton rapport PDF pour {period_label} : [Ouvrir le PDF]({report_url})",
                tool_result=_build_open_pdf_ui_request(report_url),
                plan={"tool_name": "finance_report_spending_pdf", "payload": plan_payload},
            )

        memory_for_loop = state_dict if isinstance(state_dict, dict) and state_dict else None

        logger.info(
            "agent_chat_state_loaded active_task_present=%s memory_present=%s memory_keys=%s",
            isinstance(active_task, dict),
            isinstance(memory_for_loop, dict),
            sorted(memory_for_loop.keys()) if isinstance(memory_for_loop, dict) else [],
        )

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

        return _chat_response(reply=reply_text, tool_result=safe_tool_result, plan=safe_plan)
    except HTTPException as exc:
        if exc.status_code in {401, 403}:
            raise
        logger.exception("agent_chat_http_exception", exc_info=exc)
        return _chat_response(
            reply="Une erreur est survenue côté serveur. Réessaie dans quelques secondes.",
            tool_result={"error": "internal_server_error"},
            plan=None,
        )

    except Exception:
        logger.exception(
            "agent_chat_unhandled_error",
            extra={"path": "/agent/chat", "profile_id": str(profile_id) if profile_id is not None else None},
        )
        return _chat_response(
            reply="Une erreur est survenue côté serveur. Réessaie dans quelques secondes.",
            tool_result={"error": "internal_server_error"},
            plan=None,
        )


@app.post("/agent/reset-session")
def reset_session(request: Request, authorization: str | None = Header(default=None)) -> dict[str, bool]:
    """Reset persisted chat session state for the authenticated profile."""

    auth_user_id, profile_id = _resolve_authenticated_profile(request, authorization)
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
    request: Request,
    payload: HardResetPayload,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Hard reset current authenticated profile data (debug only)."""

    if os.getenv("DEBUG_ENDPOINTS_ENABLED") != "true":
        raise HTTPException(status_code=404, detail="Not found")

    if payload.confirm is not True:
        raise HTTPException(status_code=400, detail="confirm=true is required")

    auth_user_id, profile_id = _resolve_authenticated_profile(request, authorization)
    repo = get_profiles_repository()
    repo.hard_reset_profile(profile_id=profile_id, user_id=auth_user_id)
    return {"ok": True}


@app.get("/finance/shared-expenses/suggestions")
def list_shared_expense_suggestions(
    request: Request,
    status: str = "pending",
    limit: int = 50,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """List shared expense suggestions for authenticated profile."""

    _, profile_id = _resolve_authenticated_profile(request, authorization)
    repository = _get_shared_expenses_repository_or_501()
    items = repository.list_shared_expense_suggestions(profile_id=profile_id, status=status, limit=limit)
    return {
        "items": [
            {
                "id": str(item.id),
                "transaction_id": str(item.transaction_id),
                "suggested_to_profile_id": str(item.suggested_to_profile_id),
                "suggested_split_ratio_other": str(item.suggested_split_ratio_other),
                "status": item.status,
                "confidence": item.confidence,
                "rationale": item.rationale,
                "link_id": str(item.link_id) if item.link_id else None,
                "link_pair_id": str(item.link_pair_id) if item.link_pair_id else None,
            }
            for item in items
        ]
    }


@app.post("/finance/shared-expenses/suggestions/{suggestion_id}/dismiss")
def dismiss_shared_expense_suggestion(
    request: Request,
    suggestion_id: UUID,
    payload: SharedExpenseSuggestionDismissPayload | None = None,
    authorization: str | None = Header(default=None),
) -> dict[str, bool]:
    """Dismiss a shared expense suggestion."""

    _, profile_id = _resolve_authenticated_profile(request, authorization)
    repository = _get_shared_expenses_repository_or_501()
    repository.mark_suggestion_status(
        profile_id=profile_id,
        suggestion_id=suggestion_id,
        status="dismissed",
        error=payload.reason if payload else None,
    )
    return {"ok": True}


@app.post("/finance/shared-expenses/suggestions/{suggestion_id}/apply")
def apply_shared_expense_suggestion(
    request: Request,
    suggestion_id: UUID,
    payload: SharedExpenseSuggestionApplyPayload | None = None,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Apply one shared expense suggestion and create a shared expense row."""

    _, profile_id = _resolve_authenticated_profile(request, authorization)
    repository = _get_shared_expenses_repository_or_501()

    amount: Decimal | None = None
    if payload and payload.amount is not None:
        try:
            amount = Decimal(payload.amount)
        except (InvalidOperation, ValueError) as exc:
            raise HTTPException(status_code=400, detail="invalid amount format") from exc

    if amount is None:
        suggestion = repository.get_suggestion_by_id(profile_id=profile_id, suggestion_id=suggestion_id)
        if suggestion is None:
            raise HTTPException(status_code=404, detail="suggestion not found")

        supabase_client = SupabaseClient(
            settings=SupabaseSettings(
                url=_config.supabase_url(),
                service_role_key=_config.supabase_service_role_key(),
                anon_key=_config.supabase_anon_key(),
            )
        )
        try:
            transaction_rows, _ = supabase_client.get_rows(
                table="releves_bancaires",
                query={
                    "select": "id,montant",
                    "id": f"eq.{suggestion.transaction_id}",
                    "profile_id": f"eq.{profile_id}",
                    "limit": 1,
                },
                with_count=False,
                use_anon_key=False,
            )
        except RuntimeError:
            transaction_rows = []

        if transaction_rows:
            montant_raw = transaction_rows[0].get("montant")
            try:
                montant = abs(Decimal(str(montant_raw or "0")))
                amount = montant * suggestion.suggested_split_ratio_other
            except (InvalidOperation, ValueError):
                amount = None

    if amount is None:
        raise HTTPException(status_code=400, detail="amount required")

    shared_expense_id = repository.create_shared_expense_from_suggestion(
        profile_id=profile_id,
        suggestion_id=suggestion_id,
        amount=amount,
    )
    return {
        "ok": True,
        "shared_expense_id": str(shared_expense_id) if shared_expense_id is not None else None,
    }


@app.get("/finance/bank-accounts")
def list_bank_accounts(request: Request, authorization: str | None = Header(default=None)) -> Any:
    """Return bank accounts for the authenticated profile."""

    _, profile_id = _resolve_authenticated_profile(request, authorization)
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


def _coerce_json_dict(value: Any) -> dict[str, Any]:
    """Return a dict from a dict-or-JSON-string metadata payload."""

    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except Exception as exc:
            logger.debug(
                "metadata_json_parse_failed exc_type=%s value_length=%s",
                type(exc).__name__,
                len(value),
            )
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _clean_merchant_display_name(raw_value: str) -> str:
    """Normalize merchant label for compact PDF display."""

    first_segment = raw_value.split(";", 1)[0].strip()
    if not first_segment:
        return "Inconnu"
    if len(first_segment) <= 40:
        return first_segment
    return first_segment[:39].rstrip() + "…"


def _normalize_report_category(value: str | None) -> str:
    """Normalize report category label with fallback to Autres."""

    if isinstance(value, str) and value.strip():
        cleaned = value.strip()
        if cleaned.casefold() in {"sans catégorie", "sans categorie"}:
            return "Autres"
        return cleaned
    return "Sans catégorie"


def _resolve_report_category_label(
    *,
    profile_id: UUID,
    item: dict[str, Any],
    profiles_repository: ProfilesRepository,
) -> str:
    """Resolve report category with category_id priority then fallback system key."""

    direct_label = _pick_first_non_empty_string(
        [
            item.get("categorie"),
            item.get("category_name"),
            item.get("category_label"),
            item.get("category"),
            item.get("merchant_category"),
            item.get("profile_category"),
            item.get("category_override"),
            item.get("category_norm"),
            item.get("category_display_name"),
        ]
    )
    if direct_label:
        return _normalize_report_category(direct_label)

    category_id_raw = item.get("category_id")
    if category_id_raw is not None:
        try:
            category_id = category_id_raw if isinstance(category_id_raw, UUID) else UUID(str(category_id_raw))
        except (TypeError, ValueError):
            category_id = None
        if category_id is not None:
            resolved = profiles_repository.get_profile_category_name_by_id(
                profile_id=profile_id,
                category_id=category_id,
            )
            if isinstance(resolved, str) and resolved.strip():
                resolved_norm = _normalize_report_category(resolved)
                if resolved_norm.casefold() not in {"autres", "sans catégorie", "sans categorie"}:
                    return resolved_norm

    metadata = _coerce_json_dict(item.get("metadonnees"))
    if not metadata:
        metadata = _coerce_json_dict(item.get("meta"))
    category_key = str(metadata.get("category_key") or "").strip().lower()
    if category_key and category_key != "other":
        resolved_system_label = resolve_system_category_label(category_key)
        if resolved_system_label:
            return _normalize_report_category(resolved_system_label)

    return "Autres"


def _determine_report_flow_type(*, item: dict[str, Any], category: str, amount: Decimal) -> str:
    """Determine report flow type for transaction sectioning."""

    metadata = _coerce_json_dict(item.get("metadonnees"))
    tx_kind = str(metadata.get("tx_kind") or "").strip().lower()
    if tx_kind == "transfer_internal":
        return "transfer_internal"

    if category.casefold() in {"transferts internes", "transfert interne"}:
        return "transfer_internal"

    return "income" if amount > 0 else "expense"


def _fetch_spending_transactions(
    *,
    profile_id: UUID,
    payload: dict[str, Any],
) -> tuple[list[SpendingTransactionRow], bool, bool]:
    """Fetch all period transactions for spending PDF detail page."""

    router = get_tool_router()
    query_payload = {
        "date_range": payload.get("date_range"),
        "limit": 500,
        "offset": 0,
        "include_internal_transfers": True,
    }
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
    profiles_repository = get_profiles_repository()
    merchant_entity_ids: set[UUID] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_merchant_entity_id = item.get("merchant_entity_id")
        if raw_merchant_entity_id is None:
            continue
        try:
            merchant_entity_id = (
                raw_merchant_entity_id
                if isinstance(raw_merchant_entity_id, UUID)
                else UUID(str(raw_merchant_entity_id))
            )
        except (TypeError, ValueError):
            continue
        merchant_entity_ids.add(merchant_entity_id)

    merchant_canonical_name_map: dict[UUID, str] = {}
    get_canonical_names = getattr(
        profiles_repository,
        "get_merchant_entity_canonical_names_by_ids",
        None,
    )
    if callable(get_canonical_names) and merchant_entity_ids:
        merchant_canonical_name_map = get_canonical_names(
            merchant_entity_ids=sorted(merchant_entity_ids, key=str),
        )

    for item in items:
        if not isinstance(item, dict):
            continue

        raw_amount = item.get("montant")
        try:
            amount = Decimal(str(raw_amount))
        except (InvalidOperation, TypeError, ValueError):
            continue

        date_value = item.get("date")
        date_label = str(date_value) if date_value is not None else ""

        merchant_entity_canonical_name: str | None = None
        raw_merchant_entity_id = item.get("merchant_entity_id")
        if raw_merchant_entity_id is not None:
            try:
                merchant_entity_id = (
                    raw_merchant_entity_id
                    if isinstance(raw_merchant_entity_id, UUID)
                    else UUID(str(raw_merchant_entity_id))
                )
            except (TypeError, ValueError):
                merchant_entity_id = None
            if merchant_entity_id is not None:
                merchant_entity_canonical_name = merchant_canonical_name_map.get(
                    merchant_entity_id
                )

        merchant_raw = _pick_first_non_empty_string(
            [
                merchant_entity_canonical_name,
                item.get("merchant_entity_name"),
                item.get("merchant_entity_canonical_name"),
                item.get("merchant_canonical_name"),
                item.get("merchant_display_name"),
                item.get("merchant"),
                item.get("merchant_name"),
                item.get("payee"),
                item.get("libelle"),
            ]
        ) or "Inconnu"

        category = _resolve_report_category_label(
            profile_id=profile_id,
            item=item,
            profiles_repository=profiles_repository,
        )
        merchant = _clean_merchant_display_name(merchant_raw)
        flow_type = _determine_report_flow_type(item=item, category=category, amount=amount)
        if merchant.casefold() == "inconnu" and flow_type == "transfer_internal":
            merchant = "Transfert interne"

        if category == "Sans catégorie":
            logger.debug(
                "finance_spending_report_transaction_missing_category",
                extra={
                    "profile_id": str(profile_id),
                    "transaction_date": date_label,
                    "merchant": merchant,
                },
            )

        rows.append(
            SpendingTransactionRow(
                date=date_label,
                merchant=merchant,
                category=category,
                amount=amount,
                flow_type=flow_type,
            )
        )

    rows.sort(key=lambda row: row.date)

    total = payload_dict.get("total") if isinstance(payload_dict, dict) else None
    truncated = isinstance(total, int) and total > len(rows)
    return rows, truncated, False


def _serialize_effective_spending_summary(summary: dict[str, Decimal]) -> dict[str, str]:
    """Serialize effective spending summary for JSON responses."""

    return {
        "outgoing": str(summary["outgoing"]),
        "incoming": str(summary["incoming"]),
        "net_balance": str(summary["net_balance"]),
        "effective_total": str(summary["effective_total"]),
    }


def _build_spending_report_payload(
    *,
    profile_id: UUID,
    period_start: date,
    period_end: date,
) -> dict[str, Any]:
    """Build spending report payload shared by JSON and PDF endpoints."""

    payload = {
        "date_range": {
            "start_date": period_start.isoformat(),
            "end_date": period_end.isoformat(),
        },
        "direction": RelevesDirection.DEBIT_ONLY.value,
        "include_internal_transfers": False,
    }
    tool_router = get_tool_router()
    backend_client = getattr(tool_router, "backend_client", None)
    tool_service = getattr(backend_client, "tool_service", None)
    releves_repository = getattr(tool_service, "releves_repository", None)

    cashflow_summary: dict[str, Decimal | int | str | None] = {
        "total_income": Decimal("0"),
        "total_expense": Decimal("0"),
        "net_cashflow": Decimal("0"),
        "internal_transfers": Decimal("0"),
        "transaction_count": 0,
        "currency": None,
    }
    if releves_repository is not None:
        cashflow_summary = releves_repository.compute_cashflow_summary(
            profile_id=profile_id,
            date_range=DateRange(start_date=period_start, end_date=period_end),
            bank_account_id=None,
        )

    sum_result = tool_router.call("finance_releves_sum", payload, profile_id=profile_id)
    if isinstance(sum_result, ToolError):
        raise HTTPException(status_code=400, detail=sum_result.message)

    categories_result = tool_router.call(
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

    category_totals: dict[str, Decimal] = {}
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
            resolved_name = category_name if isinstance(category_name, str) else None
            if not resolved_name or not str(resolved_name).strip():
                resolved_name = resolve_system_category_label(str(category_name or "").strip().lower())
            name = _normalize_report_category(resolved_name)
            normalized_name = "Autres" if name.casefold() in {"autres", "sans catégorie", "sans categorie"} else name
            category_totals[normalized_name] = category_totals.get(normalized_name, Decimal("0")) + amount

    total = abs(Decimal(str(sum_payload.get("total") or "0")))
    shared_repository = _try_get_shared_expenses_repository()
    effective_spending_summary = compute_effective_spending_summary_safe(
        profile_id=profile_id,
        start_date=period_start,
        end_date=period_end,
        releves_total_expense=total,
        shared_expenses_repository=shared_repository,
    )

    transactions, transactions_truncated, transactions_unavailable = _fetch_spending_transactions(
        profile_id=profile_id,
        payload=payload,
    )

    return {
        "period": {
            "start_date": period_start.isoformat(),
            "end_date": period_end.isoformat(),
            "label": f"{period_start.isoformat()} → {period_end.isoformat()}",
        },
        "currency": currency,
        "total": str(total),
        "count": int(sum_payload.get("count") or 0),
        "cashflow": {
            "total_income": str(Decimal(str(cashflow_summary.get("total_income") or "0"))),
            "total_expense": str(Decimal(str(cashflow_summary.get("total_expense") or "0"))),
            "net_cashflow": str(Decimal(str(cashflow_summary.get("net_cashflow") or "0"))),
            "internal_transfers": str(Decimal(str(cashflow_summary.get("internal_transfers") or "0"))),
            "net_including_transfers": str(
                Decimal(str(cashflow_summary.get("net_cashflow") or "0"))
                + Decimal(str(cashflow_summary.get("internal_transfers") or "0"))
            ),
            "transaction_count": int(cashflow_summary.get("transaction_count") or 0),
            "currency": str(cashflow_summary.get("currency")) if cashflow_summary.get("currency") is not None else None,
        },
        "categories": [{"name": name, "amount": str(amount)} for name, amount in category_totals.items()],
        "transactions": [
            {
                "date": row.date,
                "merchant": row.merchant,
                "category": row.category,
                "amount": str(row.amount),
                "flow_type": row.flow_type,
            }
            for row in transactions
        ],
        "transactions_truncated": transactions_truncated,
        "transactions_unavailable": transactions_unavailable,
        "effective_spending": _serialize_effective_spending_summary(effective_spending_summary),
    }


def _build_spending_report_pdf_data(payload: dict[str, Any]) -> SpendingReportData:
    """Convert report payload to PDF rendering dataclass."""

    categories_payload = payload.get("categories") if isinstance(payload.get("categories"), list) else []
    transactions_payload = payload.get("transactions") if isinstance(payload.get("transactions"), list) else []
    cashflow = payload.get("cashflow") if isinstance(payload.get("cashflow"), dict) else {}
    period = payload.get("period") if isinstance(payload.get("period"), dict) else {}
    effective_spending = payload.get("effective_spending") if isinstance(payload.get("effective_spending"), dict) else {}

    return SpendingReportData(
        period_label=str(period.get("label") or ""),
        start_date=str(period.get("start_date") or ""),
        end_date=str(period.get("end_date") or ""),
        total=Decimal(str(payload.get("total") or "0")),
        count=int(payload.get("count") or 0),
        currency=str(payload.get("currency") or "CHF"),
        cashflow_income=Decimal(str(cashflow.get("total_income") or "0")),
        cashflow_expense=Decimal(str(cashflow.get("total_expense") or "0")),
        cashflow_net=Decimal(str(cashflow.get("net_cashflow") or "0")),
        cashflow_internal_transfers=Decimal(str(cashflow.get("internal_transfers") or "0")),
        cashflow_net_including_transfers=Decimal(str(cashflow.get("net_including_transfers") or "0")),
        cashflow_transaction_count=int(cashflow.get("transaction_count") or 0),
        cashflow_currency=str(cashflow.get("currency")) if cashflow.get("currency") is not None else None,
        effective_total=Decimal(str(effective_spending.get("effective_total") or "0")),
        shared_outgoing=Decimal(str(effective_spending.get("outgoing") or "0")),
        shared_incoming=Decimal(str(effective_spending.get("incoming") or "0")),
        shared_net_balance=Decimal(str(effective_spending.get("net_balance") or "0")),
        categories=[
            SpendingCategoryRow(name=str(row.get("name") or "Autres"), amount=Decimal(str(row.get("amount") or "0")))
            for row in categories_payload
            if isinstance(row, dict)
        ],
        transactions=[
            SpendingTransactionRow(
                date=str(row.get("date") or ""),
                merchant=str(row.get("merchant") or "Inconnu"),
                category=str(row.get("category") or "Sans catégorie"),
                amount=Decimal(str(row.get("amount") or "0")),
                flow_type=str(row.get("flow_type") or "expense"),
            )
            for row in transactions_payload
            if isinstance(row, dict)
        ],
        transactions_truncated=bool(payload.get("transactions_truncated")),
        transactions_unavailable=bool(payload.get("transactions_unavailable")),
    )


@app.get("/finance/reports/spending")
def get_spending_report_json(
    request: Request,
    authorization: str | None = Header(default=None),
    start_date: str | None = None,
    end_date: str | None = None,
    month: str | None = None,
) -> dict[str, Any]:
    try:
        auth_user_id, profile_id = _resolve_authenticated_profile(request, authorization)
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
                "format": "json",
            },
        )
        return _build_spending_report_payload(profile_id=profile_id, period_start=period_start, period_end=period_end)
    except Exception:
        logger.exception("spending_report_failed", extra={"format": "json"})
        raise


@app.get("/finance/reports/spending.pdf")
def get_spending_report_pdf(
    request: Request,
    authorization: str | None = Header(default=None),
    start_date: str | None = None,
    end_date: str | None = None,
    month: str | None = None,
) -> Response:
    try:
        auth_user_id, profile_id = _resolve_authenticated_profile(request, authorization)
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
                "format": "pdf",
            },
        )

        report_payload = _build_spending_report_payload(profile_id=profile_id, period_start=period_start, period_end=period_end)

        filename_period = (
            period_start.strftime("%Y-%m") if period_start.day == 1 else f"{period_start.isoformat()}_{period_end.isoformat()}"
        )
        pdf_bytes = generate_spending_report_pdf(_build_spending_report_pdf_data(report_payload))
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="rapport-depenses-{filename_period}.pdf"'},
        )
    except Exception:
        logger.exception("spending_report_failed", extra={"format": "pdf"})
        raise




def _emit_import_job_event(
    *,
    repository: SupabaseImportJobsRepository,
    profile_id: UUID,
    job_id: UUID,
    kind: str,
    message: str,
    progress: float | None = None,
    payload: dict[str, Any] | None = None,
    job_patch: dict[str, Any] | None = None,
) -> int:
    """Persist one import progress event and optional job updates."""

    seq = repository.next_event_seq(job_id=job_id)
    repository.create_event(job_id=job_id, seq=seq, kind=kind, message=message, progress=progress, payload=payload)
    patch_payload: dict[str, Any] = {}
    if job_patch:
        patch_payload.update(job_patch)
    if progress is not None and patch_payload.get("status") in {"pending", "running"}:
        patch_payload["processed_transactions"] = patch_payload.get("processed_transactions")
    if patch_payload:
        repository.patch_job(profile_id=profile_id, job_id=job_id, payload=patch_payload)
    return seq


def _build_throttled_import_progress_emitter(
    *,
    repository: SupabaseImportJobsRepository,
    profile_id: UUID,
    job_id: UUID,
    min_interval_seconds: float = 0.8,
):
    """Return a throttled emitter for frequent progress updates."""

    last_emitted_at_by_kind: dict[str, float] = {}

    def _emit(
        *,
        kind: str,
        message: str,
        done: int,
        total: int,
        force: bool = False,
        progress: float | None = None,
        job_patch: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        total_safe = max(total, 1)
        done_safe = max(0, min(done, total_safe))
        now = time.monotonic()
        should_emit_by_count = done_safe % 25 == 0 or done_safe == total_safe
        elapsed = now - last_emitted_at_by_kind.get(kind, 0.0)
        should_emit = force or (should_emit_by_count and elapsed >= min_interval_seconds)
        if not should_emit:
            return

        emit_progress = progress if progress is not None else done_safe / total_safe
        patch_payload = dict(job_patch or {})
        patch_payload.setdefault("processed_transactions", done_safe)
        patch_payload.setdefault("total_transactions", total)
        _emit_import_job_event(
            repository=repository,
            profile_id=profile_id,
            job_id=job_id,
            kind=kind,
            message=message,
            progress=emit_progress,
            payload=payload,
            job_patch=patch_payload,
        )
        last_emitted_at_by_kind[kind] = now

    return _emit


def _run_import_job_pipeline(*, repository: SupabaseImportJobsRepository, profile_id: UUID, payload: ImportRequestPayload, job_id: UUID) -> None:
    """Execute CSV import in background and persist progress events."""

    try:
        total_transactions_hint = 1
        parsed_total_received = False
        emit_progress = _build_throttled_import_progress_emitter(
            repository=repository,
            profile_id=profile_id,
            job_id=job_id,
        )

        job = repository.get_job(profile_id=profile_id, job_id=job_id)
        persisted_result = job.result if job is not None and isinstance(getattr(job, "result", None), dict) else {}
        persisted_bank_account_id = None
        if isinstance(persisted_result, dict):
            candidate = persisted_result.get("bank_account_id")
            if isinstance(candidate, str) and candidate.strip():
                persisted_bank_account_id = candidate.strip()
        selected_bank_account_id = payload.bank_account_id or persisted_bank_account_id
        selected_bank_account_name: str | None = None
        files_payload = [{"filename": file.filename, "content_base64": file.content_base64} for file in payload.files]
        detected_bank_code = _detect_bank_code_from_import_files(files_payload)

        profiles_repository = get_profiles_repository()
        existing_accounts = profiles_repository.list_bank_accounts(profile_id=profile_id) if hasattr(profiles_repository, "list_bank_accounts") else []
        if not selected_bank_account_id:
            first_filename = payload.files[0].filename if payload.files else ""
            first_file_bytes: bytes | None = None
            if payload.files:
                try:
                    decoded = base64.b64decode(payload.files[0].content_base64, validate=False)
                    first_file_bytes = decoded[:65536]
                except Exception:
                    logger.warning("import_job_bank_detection_decode_failed profile_id=%s", profile_id)

            detection_result = _detect_bank_account_for_import(
                filename=first_filename,
                file_bytes=first_file_bytes,
                existing_accounts=existing_accounts,
            )
            if isinstance(detection_result, dict):
                selected_bank_account_id = str(detection_result.get("id") or "").strip() or None
                selected_bank_account_name = str(detection_result.get("name") or "").strip() or None
            elif detection_result in {"ambiguous", None}:
                if len(existing_accounts) == 1:
                    selected_bank_account_id = str(existing_accounts[0].get("id") or "").strip() or None
                    selected_bank_account_name = str(existing_accounts[0].get("name") or "").strip() or None
                elif len(existing_accounts) > 1:
                    selected_bank_account_id = str(existing_accounts[0].get("id") or "").strip() or None
                    selected_bank_account_name = str(existing_accounts[0].get("name") or "").strip() or None
                    if selected_bank_account_id:
                        _emit_import_job_event(
                            repository=repository,
                            profile_id=profile_id,
                            job_id=job_id,
                            kind="warning",
                            message=(
                                "Aucun compte bancaire n’a pu être détecté automatiquement. "
                                "Le premier compte a été sélectionné par défaut."
                            ),
                            progress=0.01,
                            payload={"bank_account_id": selected_bank_account_id},
                        )

        if not selected_bank_account_id and detected_bank_code:
            try:
                selected_bank_account_id = _resolve_bank_account_id_from_bank_code(
                    profiles_repository,
                    profile_id,
                    detected_bank_code,
                )
            except ValueError as exc:
                if str(exc) == "ambiguous_bank_account":
                    error_message = f"Plusieurs comptes {detected_bank_code.upper()} trouvés. Choisis le compte."
                    error_payload = {
                        "needs_account_selection": True,
                        "bank_code": detected_bank_code,
                    }
                    _emit_import_job_event(
                        repository=repository,
                        profile_id=profile_id,
                        job_id=job_id,
                        kind="error",
                        message=error_message,
                        progress=1.0,
                        payload=error_payload,
                        job_patch={"status": "error", "error_message": error_message, "result": error_payload},
                    )
                    return
                raise

            if not selected_bank_account_id:
                error_message = f"Aucun compte {detected_bank_code.upper()} trouvé. Ajoute/active ce compte."
                _emit_import_job_event(
                    repository=repository,
                    profile_id=profile_id,
                    job_id=job_id,
                    kind="error",
                    message=error_message,
                    progress=1.0,
                    payload={"bank_code": detected_bank_code},
                    job_patch={"status": "error", "error_message": error_message},
                )
                return

        if selected_bank_account_id and existing_accounts:
            for account in existing_accounts:
                account_id = str(account.get("id") or "").strip()
                if account_id != selected_bank_account_id:
                    continue
                candidate_name = str(account.get("name") or "").strip()
                if candidate_name:
                    selected_bank_account_name = candidate_name
                break

        current_app_env = _config.app_env().strip().lower()
        supabase_url_configured = bool(_config.supabase_url())
        supabase_service_role_key_configured = bool(_config.supabase_service_role_key())
        _emit_import_job_event(
            repository=repository,
            profile_id=profile_id,
            job_id=job_id,
            kind="debug",
            message=(
                "Import debug: "
                f"app_env={current_app_env}, "
                f"supabase_url={'ok' if supabase_url_configured else 'missing'}, "
                f"service_role_key={'ok' if supabase_service_role_key_configured else 'missing'}, "
                f"bank_account_id={selected_bank_account_id or 'missing'}"
            ),
            progress=0.01,
            payload={
                "app_env": current_app_env,
                "supabase_url_configured": supabase_url_configured,
                "supabase_service_role_key_configured": supabase_service_role_key_configured,
                "selected_bank_account_id": selected_bank_account_id,
            },
        )

        if detected_bank_code and selected_bank_account_id:
            account_label = selected_bank_account_name or selected_bank_account_id
            _emit_import_job_event(
                repository=repository,
                profile_id=profile_id,
                job_id=job_id,
                kind="bank_detected",
                message=f"Banque détectée : {detected_bank_code.upper()}\nCompte associé : {account_label}",
                progress=0.01,
            )

        _emit_import_job_event(
            repository=repository,
            profile_id=profile_id,
            job_id=job_id,
            kind="started",
            message="Import lancé.",
            progress=0.02,
        )
        _emit_import_job_event(
            repository=repository,
            profile_id=profile_id,
            job_id=job_id,
            kind="parsing",
            message="Lecture du fichier CSV…",
            progress=0.08,
        )

        request_payload = {
            "files": files_payload,
            "modified_action": payload.modified_action,
            "profile_id": str(profile_id),
        }
        if "import_mode" in payload.model_fields_set:
            request_payload["import_mode"] = payload.import_mode
        request_payload.setdefault("import_mode", RelevesImportMode.COMMIT.value)
        if selected_bank_account_id:
            request_payload["bank_account_id"] = selected_bank_account_id

        request_model = RelevesImportRequest.model_validate(request_payload)
        if request_model.import_mode != RelevesImportMode.COMMIT:
            logger.warning(
                "import_job_pipeline_forced_commit_mode job_id=%s profile_id=%s import_mode=%s",
                job_id,
                profile_id,
                request_model.import_mode,
            )
            request_model = request_model.model_copy(update={"import_mode": RelevesImportMode.COMMIT})
        tool_router = get_tool_router()

        def _on_import_progress(stage: str, done: int, total: int) -> None:
            nonlocal parsed_total_received, total_transactions_hint

            if stage == "parsed_total":
                parsed_total = max(total, 0)
                total_transactions_hint = max(parsed_total, 1)
                parsed_total_received = True
                _emit_import_job_event(
                    repository=repository,
                    profile_id=profile_id,
                    job_id=job_id,
                    kind="parsed",
                    message=f"Transactions détectées : {parsed_total}.",
                    progress=0.2,
                    payload={"total_transactions": parsed_total},
                    job_patch={"total_transactions": parsed_total},
                )
                emit_progress(
                    kind="categorization_progress",
                    message=f"Catégorisation… (0/{total_transactions_hint})",
                    done=0,
                    total=total_transactions_hint,
                    force=True,
                    progress=0.45,
                    job_patch={"total_llm_items": total_transactions_hint, "processed_llm_items": 0},
                )
                return

            if stage != "categorization" or not parsed_total_received:
                return

            emit_progress(
                kind="categorization_progress",
                message=f"Catégorisation… ({done}/{total})",
                done=done,
                total=total,
                progress=0.45 + (0.45 * (done / max(total, 1))),
                job_patch={"processed_llm_items": done, "total_llm_items": total},
            )

        backend_client = getattr(tool_router, "backend_client", None)
        if backend_client is not None and hasattr(backend_client, "finance_releves_import_files"):
            result_obj = backend_client.finance_releves_import_files(request=request_model, on_progress=_on_import_progress)
            result = jsonable_encoder(result_obj)
        else:
            result_obj = tool_router.call(
                "finance_releves_import_files",
                {
                    "files": files_payload,
                    "import_mode": payload.import_mode,
                    "modified_action": payload.modified_action,
                    **({"bank_account_id": selected_bank_account_id} if selected_bank_account_id else {}),
                },
                profile_id=profile_id,
            )
            result = jsonable_encoder(result_obj)
            emit_progress(
                kind="categorization_progress",
                message=f"Catégorisation… ({total_transactions_hint}/{total_transactions_hint})",
                done=total_transactions_hint,
                total=total_transactions_hint,
                force=True,
                progress=0.9,
                job_patch={"processed_llm_items": total_transactions_hint, "total_llm_items": total_transactions_hint},
            )

        if _is_tool_error_payload(result):
            error_message = str(result.get("message") or "Import interrompu.")
            _emit_import_job_event(
                repository=repository,
                profile_id=profile_id,
                job_id=job_id,
                kind="error",
                message=error_message,
                progress=1.0,
                payload={"result": result},
                job_patch={"status": "error", "error_message": error_message, "result": result},
            )
            raise RuntimeError(error_message)

        processed_transactions = None
        if isinstance(result, dict):
            result.setdefault("bank_account_id", selected_bank_account_id)
            value = result.get("transactions_imported") or result.get("imported_count")
            if isinstance(value, (int, float)):
                processed_transactions = int(value)
            date_range = _extract_import_date_range(result)
            if isinstance(date_range, dict):
                result.setdefault("import_start_date", date_range.get("start"))
                result.setdefault("import_end_date", date_range.get("end"))

        _emit_import_job_event(
            repository=repository,
            profile_id=profile_id,
            job_id=job_id,
            kind="db_insert_progress",
            message="Import en base de données… (0/1)",
            progress=0.9,
        )

        _emit_import_job_event(
            repository=repository,
            profile_id=profile_id,
            job_id=job_id,
            kind="db_insert_progress",
            message="Import en base de données… (1/1)",
            progress=0.92,
            job_patch={"processed_transactions": processed_transactions},
        )
        _emit_import_job_event(
            repository=repository,
            profile_id=profile_id,
            job_id=job_id,
            kind="done",
            message="Traitement terminé.",
            progress=1.0,
            payload={"result": result if isinstance(result, dict) else None},
            job_patch={"status": "done", "processed_transactions": processed_transactions, "result": result if isinstance(result, dict) else None},
        )
    except Exception as exc:
        logger.exception("import_job_pipeline_failed job_id=%s profile_id=%s", job_id, profile_id)
        current_job = repository.get_job(profile_id=profile_id, job_id=job_id)
        if current_job is not None and current_job.status == "error":
            return
        _emit_import_job_event(
            repository=repository,
            profile_id=profile_id,
            job_id=job_id,
            kind="error",
            message=f"Import interrompu: {exc}",
            progress=1.0,
            job_patch={"status": "error", "error_message": str(exc)},
        )


@app.post("/imports/jobs", response_model=ImportJobCreateResponse)
def create_import_job(request: Request, authorization: str | None = Header(default=None)) -> ImportJobCreateResponse:
    """Create one async import job for the authenticated profile."""

    _auth_user_id, profile_id = _resolve_authenticated_profile(request, authorization)
    repository = _get_import_jobs_repository_or_501()
    job_id = repository.create_job(profile_id=profile_id)
    return ImportJobCreateResponse(job_id=job_id)


@app.post("/imports/jobs/{job_id}/files")
def upload_import_job_file(
    request: Request,
    job_id: UUID,
    payload: ImportRequestPayload,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
) -> dict[str, bool]:
    """Attach uploaded CSV file to existing job and start background processing."""

    _auth_user_id, profile_id = _resolve_authenticated_profile(request, authorization)
    repository = _get_import_jobs_repository_or_501()
    job = repository.get_job(profile_id=profile_id, job_id=job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    invalid_filenames = [item.filename for item in payload.files if not item.filename.lower().endswith(".csv")]
    if invalid_filenames:
        raise HTTPException(status_code=400, detail="Format invalide. Pour l’instant, seul le format CSV est supporté.")

    job_patch_payload: dict[str, Any] = {"status": "running", "error_message": None}
    if payload.bank_account_id:
        existing_result = job.result if isinstance(getattr(job, "result", None), dict) else {}
        job_patch_payload["result"] = {**existing_result, "bank_account_id": payload.bank_account_id}
    repository.patch_job(profile_id=profile_id, job_id=job_id, payload=job_patch_payload)
    background_tasks.add_task(
        _run_import_job_pipeline,
        repository=repository,
        profile_id=profile_id,
        payload=payload,
        job_id=job_id,
    )
    return {"ok": True}


@app.get("/imports/jobs/{job_id}", response_model=ImportJobStatusResponse)
def get_import_job_status(
    request: Request,
    job_id: UUID,
    authorization: str | None = Header(default=None),
) -> ImportJobStatusResponse:
    """Return one job status snapshot for polling fallback."""

    _auth_user_id, profile_id = _resolve_authenticated_profile(request, authorization)
    repository = _get_import_jobs_repository_or_501()
    job = repository.get_job(profile_id=profile_id, job_id=job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return ImportJobStatusResponse(
        job_id=job.id,
        status=job.status,
        error_message=job.error_message,
        updated_at=job.updated_at,
        total_transactions=job.total_transactions,
        processed_transactions=job.processed_transactions,
        total_llm_items=job.total_llm_items,
        processed_llm_items=job.processed_llm_items,
    )


@app.post("/imports/jobs/{job_id}/finalize-chat", response_model=ChatResponse)
def finalize_import_job_chat(
    request: Request,
    job_id: UUID,
    authorization: str | None = Header(default=None),
) -> ChatResponse:
    """Finalize import chat step once async job is complete."""

    _auth_user_id, profile_id = _resolve_authenticated_profile(request, authorization)
    repository = _get_import_jobs_repository_or_501()
    profiles_repository = get_profiles_repository()
    job = repository.get_job(profile_id=profile_id, job_id=job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status != "done":
        raise HTTPException(status_code=409, detail="job not done")

    chat_state = _normalize_chat_state(
        profiles_repository.get_chat_state(profile_id=profile_id, user_id=_auth_user_id)
    )
    state_dict = dict(chat_state.get("state")) if isinstance(chat_state.get("state"), dict) else {}
    if isinstance(job.result, dict):
        start_date = job.result.get("import_start_date")
        end_date = job.result.get("import_end_date")
        bank_account_id = job.result.get("bank_account_id")
        resolved_start_date: str | None = None
        resolved_end_date: str | None = None

        if isinstance(start_date, str) and isinstance(end_date, str) and start_date.strip() and end_date.strip():
            try:
                start_value = date.fromisoformat(start_date.strip())
                end_value = date.fromisoformat(end_date.strip())
                resolved_start_date = min(start_value, end_value).isoformat()
                resolved_end_date = max(start_value, end_value).isoformat()
            except ValueError:
                resolved_start_date = None
                resolved_end_date = None

        if resolved_start_date and resolved_end_date:
            last_import_payload: dict[str, Any] = {
                "date_range": {
                    "start_date": resolved_start_date,
                    "end_date": resolved_end_date,
                }
            }
            if isinstance(bank_account_id, str) and bank_account_id.strip():
                last_import_payload["bank_account_id"] = bank_account_id.strip()
            state_dict["last_import"] = last_import_payload

            last_query = state_dict.get("last_query") if isinstance(state_dict.get("last_query"), dict) else {}
            filters = last_query.get("filters") if isinstance(last_query.get("filters"), dict) else {}
            filters["date_range"] = {
                "start_date": resolved_start_date,
                "end_date": resolved_end_date,
            }
            if isinstance(bank_account_id, str) and bank_account_id.strip():
                filters["bank_account_id"] = bank_account_id.strip()
            last_query["filters"] = filters
            state_dict["last_query"] = last_query

    global_state = state_dict.get("global_state") if _is_valid_global_state(state_dict.get("global_state")) else None
    updated_global_state = _normalize_onboarding_step_substep(
        _build_onboarding_global_state(
            global_state,
            onboarding_step="report",
            onboarding_substep="report_offer",
        )
    )

    state_dict["global_state"] = updated_global_state

    registry = get_loop_registry()
    loop_reply = route_message(
        message="__import_done__",
        current_loop=None,
        global_state=updated_global_state,
        services={
            "profiles_repository": profiles_repository,
            "tool_router": get_tool_router(),
            "global_state": updated_global_state,
            "state": state_dict,
        },
        profile_id=profile_id,
        user_id=_auth_user_id,
        llm_judge=None,
        registry=registry,
    )

    state_dict["global_state"] = updated_global_state
    if loop_reply.next_loop is None:
        state_dict.pop("loop", None)
    else:
        state_dict["loop"] = serialize_loop_context(loop_reply.next_loop)
    updated_chat_state = dict(chat_state)
    updated_chat_state["state"] = state_dict
    profiles_repository.update_chat_state(
        profile_id=profile_id,
        user_id=_auth_user_id,
        chat_state=updated_chat_state,
    )

    return ChatResponse(
        reply=_build_import_done_reply(job.total_transactions),
        tool_result=_build_quick_reply_yes_no_ui_action(),
    )


@app.get("/imports/jobs/{job_id}/events")
async def stream_import_job_events(
    request: Request,
    job_id: UUID,
    authorization: str | None = Header(default=None),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    last_event_id_query: int | None = Query(default=None, alias="last_event_id"),
) -> StreamingResponse:
    """Stream persisted import job progress events via SSE."""

    _auth_user_id, profile_id = _resolve_authenticated_profile(request, authorization)
    repository = _get_import_jobs_repository_or_501()
    job = repository.get_job(profile_id=profile_id, job_id=job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    if isinstance(last_event_id, str) and last_event_id.isdigit():
        start_seq = int(last_event_id)
    elif isinstance(last_event_id_query, int) and last_event_id_query >= 0:
        start_seq = last_event_id_query
    else:
        start_seq = 0

    async def _event_stream():
        last_seq = start_seq
        while True:
            if await request.is_disconnected():
                break

            events = repository.list_events_since(job_id=job_id, after_seq=last_seq, limit=300)
            for event in events:
                last_seq = event.seq
                payload_data = {
                    "seq": event.seq,
                    "kind": event.kind,
                    "message": event.message,
                    "progress": event.progress,
                    "payload": event.payload,
                }
                yield f"id: {event.seq}\nevent: progress\ndata: {json.dumps(payload_data, ensure_ascii=False)}\n\n"

            current_job = repository.get_job(profile_id=profile_id, job_id=job_id)
            if current_job is not None and current_job.status in {"done", "error"}:
                if not events:
                    break
            await asyncio.sleep(0.8)

    return StreamingResponse(_event_stream(), media_type="text/event-stream")


@app.post("/finance/releves/import")
def import_releves(request: Request, payload: ImportRequestPayload, authorization: str | None = Header(default=None)) -> Any:
    """Import bank statements using backend tool router."""

    auth_user_id, profile_id = _resolve_authenticated_profile(request, authorization)
    files_payload = [
        {"filename": import_file.filename, "content_base64": import_file.content_base64}
        for import_file in payload.files
    ]
    invalid_filenames = [
        import_file.filename
        for import_file in payload.files
        if not import_file.filename.lower().endswith(".csv")
    ]
    if invalid_filenames:
        return {
            "ok": False,
            "type": "error",
            "error": {
                "code": "invalid_file_type",
                "message": "Format invalide. Pour l’instant, seul le format CSV est supporté.",
                "details": {"filenames": invalid_filenames},
            },
            "message": "Format invalide. Pour l’instant, seul le format CSV est supporté.",
        }

    tool_payload: dict[str, Any] = {
        "files": [
            {"filename": import_file.filename, "content_base64": import_file.content_base64}
            for import_file in payload.files
        ],
        "import_mode": payload.import_mode,
        "modified_action": payload.modified_action,
    }
    selected_bank_account_id = payload.bank_account_id
    selected_bank_account_name: str | None = None
    if not selected_bank_account_id:
        profiles_repository = get_profiles_repository()
        existing_accounts = profiles_repository.list_bank_accounts(profile_id=profile_id) if hasattr(profiles_repository, "list_bank_accounts") else []
        first_filename = payload.files[0].filename if payload.files else ""
        first_file_bytes: bytes | None = None
        if payload.files:
            try:
                decoded = base64.b64decode(payload.files[0].content_base64, validate=False)
                first_file_bytes = decoded[:65536]
            except Exception:
                logger.warning("import_bank_detection_decode_failed profile_id=%s", profile_id)
        detection_result = _detect_bank_account_for_import(
            filename=first_filename,
            file_bytes=first_file_bytes,
            existing_accounts=existing_accounts,
        )
        if isinstance(detection_result, dict):
            selected_bank_account_id = str(detection_result.get("id") or "") or None
            selected_bank_account_name = str(detection_result.get("name") or "").strip() or None
        elif detection_result == "ambiguous":
            chat_state = _normalize_chat_state(
                profiles_repository.get_chat_state(profile_id=profile_id, user_id=auth_user_id)
            )
            state = chat_state.get("state")
            state_dict = dict(state) if isinstance(state, dict) else {}
            import_context = state_dict.get("import_context") if isinstance(state_dict.get("import_context"), dict) else {}
            import_context["pending_files"] = files_payload
            import_context["clarification_accounts"] = [
                {"id": str(account.get("id") or ""), "name": str(account.get("name") or "").strip()}
                for account in existing_accounts
                if str(account.get("name") or "").strip()
            ]
            state_dict["import_context"] = import_context
            updated_chat_state = dict(chat_state)
            updated_chat_state["state"] = state_dict
            profiles_repository.update_chat_state(
                profile_id=profile_id,
                user_id=auth_user_id,
                chat_state=updated_chat_state,
            )
            account_names = " / ".join(account["name"] for account in import_context["clarification_accounts"])
            return {
                "ok": False,
                "type": "clarification",
                "message": f"J’ai trouvé plusieurs comptes: {account_names}. Lequel correspond à ce relevé ?",
                "clarification_type": "bank_account_for_import",
            }

    if selected_bank_account_id:
        tool_payload["bank_account_id"] = selected_bank_account_id

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
    response_payload["bank_account_id"] = selected_bank_account_id

    bank_account_name = response_payload.get("bank_account_name")
    if not isinstance(bank_account_name, str) or not bank_account_name.strip():
        bank_account_name = None
        if selected_bank_account_id:
            bank_accounts_result = get_tool_router().call("finance_bank_accounts_list", {}, profile_id=profile_id)
            if not isinstance(bank_accounts_result, ToolError):
                encoded_accounts_result = jsonable_encoder(bank_accounts_result)
                if isinstance(encoded_accounts_result, dict):
                    account_items = encoded_accounts_result.get("items")
                    if isinstance(account_items, list):
                        for account in account_items:
                            if not isinstance(account, dict):
                                continue
                            if str(account.get("id")) == str(selected_bank_account_id):
                                candidate_name = account.get("name")
                                if isinstance(candidate_name, str) and candidate_name.strip():
                                    bank_account_name = candidate_name
                                break
    if bank_account_name is None and selected_bank_account_name:
        bank_account_name = selected_bank_account_name
    response_payload["bank_account_name"] = bank_account_name

    try:
        profiles_repository = get_profiles_repository()
        chat_state = _normalize_chat_state(
            profiles_repository.get_chat_state(profile_id=profile_id, user_id=auth_user_id)
        )
        state = chat_state.get("state")
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

        updated_chat_state = dict(chat_state)
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
        response_payload["merchant_suggestions_created_count"] = max(
            int(response_payload.get("merchant_suggestions_created_count") or 0),
            int(merchant_link_summary["suggestions_created_count"]),
        )
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
                elif payload.import_mode == "analyze":
                    merchant_alias_auto_resolve_payload["skipped_reason"] = "merchant_alias_auto_resolve_analyze_mode"
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
    request: Request,
    payload: MerchantAliasResolvePayload,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Resolve pending/failed map_alias suggestions for authenticated profile."""

    if not _config.llm_enabled():
        raise HTTPException(status_code=400, detail="LLM is disabled (set AGENT_LLM_ENABLED=1)")

    _, profile_id = _resolve_authenticated_profile(request, authorization)
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
    response_payload = dict(jsonable_encoder(stats))
    bootstrap_summary = _bootstrap_merchants_from_imported_releves(
        profiles_repository=profiles_repository,
        profile_id=profile_id,
        limit=2000,
    )
    response_payload["bootstrap_summary"] = bootstrap_summary

    return response_payload




@app.get("/finance/transactions/pending")
def get_pending_transactions(request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """List/count pending categorization transactions for authenticated profile."""

    _, profile_id = _resolve_authenticated_profile(request, authorization)
    tool_router = get_tool_router()
    backend_client = getattr(tool_router, "backend_client", None)
    tool_service = getattr(backend_client, "tool_service", None)
    releves_repository = getattr(tool_service, "releves_repository", None)
    if releves_repository is None:
        return {
            "count_total": 0,
            "count_twint_p2p_pending": 0,
            "items": [],
        }
    rows = releves_repository.list_pending_categorization_releves(profile_id=profile_id, limit=50)

    items: list[dict[str, Any]] = []
    twint_pending_count = 0
    for row in rows:
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        category_key = str(meta.get("category_key") or "").strip().lower()
        category_status = str(meta.get("category_status") or "").strip().lower()
        if category_status != "pending" and category_key != "twint_p2p_pending":
            continue

        item = {
            "id": row.get("id"),
            "date": row.get("date"),
            "montant": row.get("montant"),
            "devise": row.get("devise"),
            "libelle": row.get("libelle"),
            "payee": row.get("payee"),
            "categorie": row.get("categorie"),
            "meta": {
                "category_key": meta.get("category_key"),
                "category_status": meta.get("category_status"),
            },
        }
        if _is_internal_transfer_payload(item):
            continue
        if category_key == "twint_p2p_pending":
            twint_pending_count += 1
        items.append(item)

    return {
        "count_total": len(items),
        "count_twint_p2p_pending": twint_pending_count,
        "items": items[:50],
    }

@app.get("/finance/merchants/aliases/pending-count")
def get_pending_merchant_aliases_count(request: Request, authorization: str | None = Header(default=None)) -> dict[str, int]:
    """Return count of pending/failed map_alias suggestions for authenticated profile."""

    _, profile_id = _resolve_authenticated_profile(request, authorization)
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
    request: Request,
    payload: ResolvePendingMerchantAliasesPayload | None = None,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Resolve pending/failed map_alias suggestions in multiple batches for authenticated profile."""

    if not _config.llm_enabled():
        raise HTTPException(status_code=400, detail="LLM is disabled (set AGENT_LLM_ENABLED=1)")

    _, profile_id = _resolve_authenticated_profile(request, authorization)
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
    request: Request,
    payload: MerchantSuggestionsListPayload,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _, profile_id = _resolve_authenticated_profile(request, authorization)
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
    request: Request,
    payload: MerchantSuggestionApplyPayload,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _, profile_id = _resolve_authenticated_profile(request, authorization)
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
def rename_merchant(request: Request, payload: RenameMerchantPayload, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Rename one merchant for the authenticated profile."""

    _, profile_id = _resolve_authenticated_profile(request, authorization)
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
def merge_merchants(request: Request, payload: MergeMerchantsPayload, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Merge source merchant into target merchant for the authenticated profile."""

    _, profile_id = _resolve_authenticated_profile(request, authorization)
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
