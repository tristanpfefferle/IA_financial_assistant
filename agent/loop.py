"""Minimal deterministic agent loop (no LLM calls)."""

from __future__ import annotations

from dataclasses import dataclass

from agent.tool_router import ToolRouter


@dataclass(slots=True)
class AgentReply:
    """Serializable chat output for API responses."""

    reply: str
    tool_result: dict[str, object] | None = None


@dataclass(slots=True)
class AgentLoop:
    tool_router: ToolRouter

    def handle_user_message(self, message: str) -> AgentReply:
        normalized_message = message.strip()

        if normalized_message.lower() == "ping":
            return AgentReply(reply="pong")

        if normalized_message.lower().startswith("search:"):
            search_term = normalized_message.split(":", maxsplit=1)[1].strip()
            result = self.tool_router.call(
                "finance.transactions.search",
                {
                    "search": search_term,
                    "limit": 50,
                    "offset": 0,
                },
            )
            return AgentReply(
                reply=f"Voici les transactions correspondant à '{search_term}'.",
                tool_result=result.model_dump(mode="json"),
            )

        return AgentReply(reply="Commandes disponibles: 'ping' ou 'search: <term>'.")
