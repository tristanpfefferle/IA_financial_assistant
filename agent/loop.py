"""Minimal agent loop skeleton (no business logic)."""

from __future__ import annotations

from dataclasses import dataclass

from agent.tool_router import ToolRouter


@dataclass(slots=True)
class AgentLoop:
    tool_router: ToolRouter

    def handle_user_message(self, message: str) -> str:
        if message.strip().lower() == "ping":
            return "pong"

        # Placeholder orchestration flow; model/tool selection to be implemented.
        result = self.tool_router.call("finance.transactions.search", {})
        return f"Agent placeholder response: {result}"
