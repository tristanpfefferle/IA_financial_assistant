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




def test_llm_shadow_true_string(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AGENT_LLM_SHADOW", "true")

    assert config.llm_shadow() is True


def test_llm_shadow_forced_off_in_ci_env(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "ci")
    monkeypatch.setenv("AGENT_LLM_SHADOW", "true")

    assert config.llm_shadow() is False

def test_llm_model_uses_default_when_missing(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_LLM_MODEL", raising=False)

    assert config.llm_model() == "gpt-5"


def test_cors_allow_origins_defaults_in_dev(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)

    assert config.cors_allow_origins() == ["http://localhost:5173", "http://127.0.0.1:5173"]


def test_cors_allow_origins_parses_comma_separated_list(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://a.com, https://b.com")

    assert config.cors_allow_origins() == ["https://a.com", "https://b.com"]


def test_cors_allow_origins_uses_ui_origin_in_prod(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)
    monkeypatch.setenv("UI_ORIGIN", "https://ia-financial-assistant-ui.onrender.com")

    assert config.cors_allow_origins() == ["https://ia-financial-assistant-ui.onrender.com"]


def test_cors_allow_origins_warns_and_defaults_to_empty_in_prod(monkeypatch, caplog) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)
    monkeypatch.delenv("UI_ORIGIN", raising=False)

    assert config.cors_allow_origins() == []
    assert "cors_allow_origins_empty_in_prod" in caplog.text
