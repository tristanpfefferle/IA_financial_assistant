"""Tool router placeholder.

The agent never queries DB directly; it only calls backend client interfaces.
"""

from agent.clients.backend_client import BackendClient


class ToolRouter:
    """Maps intents to backend tool calls."""

    def __init__(self, backend_client: BackendClient | None = None) -> None:
        self.backend_client = backend_client or BackendClient()

    def route(self, user_message: str) -> str:
        """Placeholder router that always calls a mock backend ping."""
        _ = user_message
        return self.backend_client.ping()
