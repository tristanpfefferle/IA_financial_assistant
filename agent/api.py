"""FastAPI entrypoint for agent HTTP endpoints."""

from __future__ import annotations

from functools import lru_cache

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from shared import config as _config
from agent.factory import build_agent_loop
from agent.loop import AgentLoop


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
def agent_chat(payload: ChatRequest) -> ChatResponse:
    """Handle a user chat message through the agent loop."""

    agent_reply = get_agent_loop().handle_user_message(payload.message)
    return ChatResponse(reply=agent_reply.reply, tool_result=agent_reply.tool_result, plan=agent_reply.plan)
