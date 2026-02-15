"""Composition root for agent orchestration."""

from __future__ import annotations

from agent.backend_client import BackendClient
from agent.llm_planner import LLMPlanner
from agent.loop import AgentLoop
from agent.tool_router import ToolRouter
from backend.factory import build_backend_tool_service
from shared import config


def build_agent_loop() -> AgentLoop:
    """Build an executable in-process agent loop wiring all dependencies."""

    backend_tool_service = build_backend_tool_service()
    backend_client = BackendClient(tool_service=backend_tool_service)
    tool_router = ToolRouter(backend_client=backend_client)
    llm_planner: LLMPlanner | None = None

    if config.llm_enabled():
        llm_planner = LLMPlanner(strict=config.llm_strict())

    return AgentLoop(
        tool_router=tool_router,
        llm_planner=llm_planner,
    )
