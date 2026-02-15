"""Tests for shared configuration helpers."""

from shared import config


def test_llm_enabled_defaults_to_false(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_LLM_ENABLED", raising=False)

    assert config.llm_enabled() is False


def test_llm_enabled_true_string(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LLM_ENABLED", "true")

    assert config.llm_enabled() is True


def test_llm_model_uses_default_when_missing(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_LLM_MODEL", raising=False)

    assert config.llm_model() == "gpt-5"
