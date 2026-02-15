"""Agent loop placeholder (no business logic)."""

from agent.tools.router import ToolRouter


class AgentLoop:
    """Coordinates prompts, tool selection and final response rendering."""

    def __init__(self) -> None:
        self.tool_router = ToolRouter()

    def handle_user_message(self, message: str) -> str:
        """Process user message via tool-routing strategy placeholder."""
        tool_result = self.tool_router.route(message)
        return f"[placeholder] Réponse agent basée sur: {tool_result}"
