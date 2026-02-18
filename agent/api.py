"""FastAPI entrypoint for agent HTTP endpoints."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any
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
from backend.factory import build_backend_tool_service
from backend.auth.supabase_auth import UnauthorizedError, get_user_from_bearer_token
from backend.db.supabase_client import SupabaseClient, SupabaseSettings
from backend.repositories.profiles_repository import SupabaseProfilesRepository
from shared.models import ToolError


logger = logging.getLogger(__name__)


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
def agent_chat(payload: ChatRequest, authorization: str | None = Header(default=None)) -> ChatResponse:
    """Handle a user chat message through the agent loop."""

    logger.info("agent_chat_received message_length=%s", len(payload.message))
    profile_id: UUID | None = None
    try:
        auth_user_id, profile_id = _resolve_authenticated_profile(authorization)
        profiles_repository = get_profiles_repository()

        chat_state = profiles_repository.get_chat_state(profile_id=profile_id, user_id=auth_user_id)
        active_task = chat_state.get("active_task") if isinstance(chat_state, dict) else None
        memory = chat_state.get("state") if isinstance(chat_state, dict) else None
        logger.info(
            "agent_chat_state_loaded active_task_present=%s memory_present=%s memory_keys=%s",
            isinstance(active_task, dict),
            isinstance(memory, dict),
            sorted(memory.keys()) if isinstance(memory, dict) else [],
        )

        agent_reply = get_agent_loop().handle_user_message(
            payload.message,
            profile_id=profile_id,
            active_task=active_task if isinstance(active_task, dict) else None,
            memory=memory if isinstance(memory, dict) else None,
        )

        response_plan = dict(agent_reply.plan) if isinstance(agent_reply.plan, dict) else agent_reply.plan

        memory_update = getattr(agent_reply, "memory_update", None)
        should_update_chat_state = (
            agent_reply.should_update_active_task
            or isinstance(memory_update, dict)
        )

        if should_update_chat_state:
            updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
            if agent_reply.should_update_active_task:
                if agent_reply.active_task is None:
                    updated_chat_state.pop("active_task", None)
                else:
                    updated_chat_state["active_task"] = jsonable_encoder(agent_reply.active_task)

            if isinstance(memory_update, dict):
                existing_state = updated_chat_state.get("state")
                merged_state = (
                    dict(existing_state)
                    if isinstance(existing_state, dict)
                    else {}
                )
                merged_state.update(jsonable_encoder(memory_update))
                updated_chat_state["state"] = merged_state

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

        return ChatResponse(reply=agent_reply.reply, tool_result=safe_tool_result, plan=safe_plan)
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
