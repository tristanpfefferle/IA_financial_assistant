"""FastAPI entrypoint for agent HTTP endpoints."""

from __future__ import annotations

import logging
from functools import lru_cache
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException
from fastapi.requests import Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from shared import config as _config
from agent.factory import build_agent_loop
from agent.loop import AgentLoop
from backend.auth.supabase_auth import UnauthorizedError, get_user_from_bearer_token
from backend.db.supabase_client import SupabaseClient, SupabaseSettings
from backend.repositories.profiles_repository import SupabaseProfilesRepository


logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    """Incoming chat request payload."""

    message: str


class ChatResponse(BaseModel):
    """Outgoing chat response payload."""

    reply: str
    tool_result: dict[str, object] | None
    plan: dict[str, object] | None = None


@lru_cache(maxsize=1)
def get_agent_loop() -> AgentLoop:
    """Create and cache the agent loop once per process."""

    return build_agent_loop()


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


app = FastAPI(title="IA Financial Assistant Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_config.cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.get("/health")
def health() -> dict[str, str]:
    """Healthcheck endpoint."""

    return {"status": "ok"}


@app.post("/agent/chat", response_model=ChatResponse)
def agent_chat(payload: ChatRequest, authorization: str | None = Header(default=None)) -> ChatResponse:
    """Handle a user chat message through the agent loop."""

    logger.info("agent_chat_received message_length=%s", len(payload.message))
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

    chat_state = profiles_repository.get_chat_state(profile_id=profile_id)
    active_task = chat_state.get("active_task") if isinstance(chat_state, dict) else None

    try:
        agent_reply = get_agent_loop().handle_user_message(
            payload.message,
            profile_id=profile_id,
            active_task=active_task if isinstance(active_task, dict) else None,
        )
    except Exception:
        logger.exception("agent_chat_failed profile_id=%s", profile_id)
        raise

    if agent_reply.should_update_active_task:
        updated_chat_state = dict(chat_state) if isinstance(chat_state, dict) else {}
        if agent_reply.active_task is None:
            updated_chat_state.pop("active_task", None)
        else:
            updated_chat_state["active_task"] = agent_reply.active_task
        profiles_repository.update_chat_state(profile_id=profile_id, chat_state=updated_chat_state)

    tool_name = agent_reply.plan.get("tool_name") if agent_reply.plan is not None else None
    logger.info("agent_chat_completed tool_name=%s", tool_name)
    return ChatResponse(reply=agent_reply.reply, tool_result=agent_reply.tool_result, plan=agent_reply.plan)
