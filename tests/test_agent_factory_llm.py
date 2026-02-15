from agent.factory import build_agent_loop


def test_llm_planner_not_injected_when_disabled(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LLM_ENABLED", "0")
    monkeypatch.setenv("APP_ENV", "test")

    loop = build_agent_loop()

    assert loop.llm_planner is None


def test_llm_planner_injected_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "dev")

    loop = build_agent_loop()

    assert loop.llm_planner is not None
