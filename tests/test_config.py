"""Tests for shared configuration helpers."""

from shared import config


def test_llm_enabled_defaults_to_false(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_LLM_ENABLED", raising=False)

    assert config.llm_enabled() is False


def test_llm_enabled_true_string(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AGENT_LLM_ENABLED", "true")

    assert config.llm_enabled() is True


def test_llm_enabled_forced_off_in_test_env(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AGENT_LLM_ENABLED", "true")

    assert config.llm_enabled() is False


def test_llm_model_uses_default_when_missing(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_LLM_MODEL", raising=False)

    assert config.llm_model() == "gpt-5"
