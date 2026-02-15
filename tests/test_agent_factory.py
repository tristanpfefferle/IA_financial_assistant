"""Integration-like tests for agent composition root."""

from agent.factory import build_agent_loop


def test_build_agent_loop_and_handle_ping() -> None:
    agent_loop = build_agent_loop()

    result = agent_loop.handle_user_message("ping")
    assert result.reply == "pong"
    assert result.tool_result is None
