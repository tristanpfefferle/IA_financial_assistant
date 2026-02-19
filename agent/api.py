"""FastAPI entrypoint for agent HTTP endpoints."""

from __future__ import annotations

import logging
import inspect
import re
import unicodedata
from functools import lru_cache
from typing import Any
from datetime import date
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from fastapi.requests import Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from shared import config as _config
from agent.backend_client import BackendClient
from agent.llm_planner import LLMPlanner
from agent.loop import AgentLoop
from agent.tool_router import ToolRouter
from agent.bank_catalog import extract_canonical_banks
from backend.factory import build_backend_tool_service
from backend.auth.supabase_auth import UnauthorizedError, get_user_from_bearer_token
from backend.db.supabase_client import SupabaseClient, SupabaseSettings
from backend.repositories.profiles_repository import SupabaseProfilesRepository
from shared.models import ToolError


logger = logging.getLogger(__name__)


_GLOBAL_STATE_MODES = {"onboarding", "guided_budget", "free_chat"}
_GLOBAL_STATE_ONBOARDING_STEPS = {"profile", "bank_accounts", "import", "categories", "budget", None}
_GLOBAL_STATE_ONBOARDING_SUBSTEPS = {
    "profile_collect",
    "profile_confirm",
    "bank_accounts_collect",
    "bank_accounts_confirm",
    "import_select_account",
    None,
}
_PROFILE_COMPLETION_FIELDS = ("first_name", "last_name", "birth_date")
_ONBOARDING_NAME_PATTERN = re.compile(
    r"^\s*([A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'\-]+)\s+([A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'\-]+)\s*$"
)
_ONBOARDING_BIRTH_DATE_PATTERN = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_ONBOARDING_BIRTH_DATE_DOT_PATTERN = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
_ONBOARDING_BIRTH_DATE_SLASH_PATTERN = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_ONBOARDING_BIRTH_DATE_MONTH_NAME_PATTERN = re.compile(r"^(\d{1,2})\s+([a-z]+)\s+(\d{4})$")
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


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip().lower()).encode("ascii", "ignore").decode("ascii")
    return " ".join(normalized.split())


def _is_yes(message: str) -> bool:
    return _normalize_text(message) in _YES_VALUES


def _is_no(message: str) -> bool:
    return _normalize_text(message) in _NO_VALUES


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
        return "(Pour continuer l’onboarding : indique ton prénom, nom et date de naissance.)"
    if substep == "profile_confirm":
        return "(Pour continuer l’onboarding : réponds OUI/NON pour confirmer le profil.)"
    if substep == "bank_accounts_collect":
        return "(Pour continuer l’onboarding : indique les banques à ajouter.)"
    if substep == "bank_accounts_confirm":
        return "(Pour continuer l’onboarding : réponds OUI/NON à la question sur les comptes.)"
    if substep == "import_select_account":
        return "(Pour continuer : indique le compte à importer.)"
    return None


def _extract_name_from_message(message: str) -> tuple[str, str] | None:
    match = _ONBOARDING_NAME_PATTERN.match(message)
    if not match:
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



class ChatRequest(BaseModel):
    """Incoming chat request payload."""

    message: str


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
                        extracted_name = _extract_name_from_message(message)
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

                        extracted_birth_date = _extract_birth_date_from_message(message)
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

                    if _is_profile_complete(profile_fields):
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
                        return ChatResponse(
                            reply=(
                                "Merci. Résumé profil: "
                                f"{profile_fields.get('first_name')} {profile_fields.get('last_name')}, "
                                f"date de naissance {profile_fields.get('birth_date')}. "
                                "Confirmez-vous que ces informations sont correctes ? (OUI/NON)"
                            ),
                            tool_result=None,
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
                    return ChatResponse(
                        reply=(
                            "Pour démarrer, j’ai besoin de ton prénom, nom et date de naissance. "
                            "Tu peux écrire 'Prénom Nom' puis ta date de naissance (YYYY-MM-DD)."
                        ),
                        tool_result=None,
                        plan=None,
                    )

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
                            reply="Profil confirmé ✅ Indique-moi tes banques à ajouter (ex: UBS, Revolut).",
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
                        state_dict["global_state"] = updated_global_state
                        updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
                        updated_chat_state["state"] = state_dict
                        profiles_repository.update_chat_state(
                            profile_id=profile_id,
                            user_id=auth_user_id,
                            chat_state=updated_chat_state,
                        )
                        return ChatResponse(
                            reply=f"J’ai déjà ces comptes: {accounts_display}. Voulez-vous en ajouter d’autres ? (OUI/NON)",
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
                    return ChatResponse(
                        reply=(
                            f"Comptes actuels: {accounts_display}. "
                            "Voulez-vous créer encore d'autres comptes bancaires ? (OUI/NON)"
                        ),
                        tool_result=None,
                        plan=None,
                    )

                if substep == "bank_accounts_confirm":
                    if _is_yes(payload.message):
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
                            reply="Ok, indique-moi les banques à ajouter (ex: UBS, Revolut).",
                            tool_result=None,
                            plan=None,
                        )
                    if _is_no(payload.message):
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
                        profiles_repository.update_chat_state(
                            profile_id=profile_id,
                            user_id=auth_user_id,
                            chat_state=updated_chat_state,
                        )
                        return ChatResponse(
                            reply=(
                                "Parfait. On passe à l’import des relevés. "
                                "Quel compte veux-tu importer ?"
                            ),
                            tool_result=None,
                            plan=None,
                        )
                    return ChatResponse(reply="Réponds OUI ou NON.", tool_result=None, plan=None)

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
                return ChatResponse(
                    reply="Parfait. Envoie le fichier CSV/PDF du compte sélectionné.",
                    tool_result=None,
                    plan=None,
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
        reminder_state = global_state if _is_valid_global_state(global_state) else None
        has_valid_memory_update_global_state = False
        if isinstance(memory_update, dict):
            memory_update_global_state = None
            state_part = memory_update.get("state")
            if isinstance(state_part, dict):
                memory_update_global_state = state_part.get("global_state")
            if _is_valid_global_state(memory_update_global_state):
                reminder_state = memory_update_global_state
                has_valid_memory_update_global_state = True

        updated_chat_global_state = None
        if isinstance(updated_chat_state, dict):
            updated_chat_global_state = updated_chat_state.get("state", {}).get("global_state")
        if _is_valid_global_state(updated_chat_global_state) and not has_valid_memory_update_global_state:
            reminder_state = updated_chat_global_state

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


@app.get("/finance/bank-accounts")
def list_bank_accounts(authorization: str | None = Header(default=None)) -> Any:
    """Return bank accounts for the authenticated profile."""

    _, profile_id = _resolve_authenticated_profile(authorization)
    result = get_tool_router().call("finance_bank_accounts_list", {}, profile_id=profile_id)
    if isinstance(result, ToolError):
        raise HTTPException(status_code=400, detail=result.message)
    return jsonable_encoder(result)


@app.post("/finance/releves/import")
def import_releves(payload: ImportRequestPayload, authorization: str | None = Header(default=None)) -> Any:
    """Import bank statements using backend tool router."""

    _, profile_id = _resolve_authenticated_profile(authorization)
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
    return jsonable_encoder(result)
