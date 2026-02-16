"""FastAPI entrypoint for agent HTTP endpoints."""

from __future__ import annotations

from functools import lru_cache

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from shared import config as _config
from agent.factory import build_agent_loop
from agent.loop import AgentLoop
from backend.auth.supabase_auth import UnauthorizedError, get_user_from_bearer_token
from backend.db.supabase_client import SupabaseClient, SupabaseSettings
from backend.repositories.profiles_repository import SupabaseProfilesRepository


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


@app.get("/health")
def health() -> dict[str, str]:
    """Healthcheck endpoint."""

    return {"status": "ok"}


@app.post("/agent/chat", response_model=ChatResponse)
def agent_chat(payload: ChatRequest, authorization: str | None = Header(default=None)) -> ChatResponse:
    """Handle a user chat message through the agent loop."""

    token = _extract_bearer_token(authorization)
    try:
        user_payload = get_user_from_bearer_token(token)
    except UnauthorizedError as exc:
        raise HTTPException(status_code=401, detail="Unauthorized") from exc

    email = user_payload.get("email")
    if not isinstance(email, str) or not email:
        raise HTTPException(status_code=401, detail="Unauthorized")

    profile_id = get_profiles_repository().get_profile_id_by_email(email)
    if profile_id is None:
        raise HTTPException(status_code=401, detail="No profile linked to authenticated user")

    agent_reply = get_agent_loop().handle_user_message(payload.message, profile_id=profile_id)
    return ChatResponse(reply=agent_reply.reply, tool_result=agent_reply.tool_result, plan=agent_reply.plan)
