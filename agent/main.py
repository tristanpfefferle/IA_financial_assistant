"""Agent entrypoint placeholder."""

from agent.loop import AgentLoop


def create_agent_loop() -> AgentLoop:
    """Factory used by local API/server integrations."""
    return AgentLoop()
