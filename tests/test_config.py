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


def test_llm_gated_true_string(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AGENT_LLM_GATED", "true")

    assert config.llm_gated() is True


def test_llm_gated_forced_off_in_test_env(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AGENT_LLM_GATED", "true")

    assert config.llm_gated() is False


def test_llm_allowed_tools_defaults(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_LLM_ALLOWED_TOOLS", raising=False)

    assert config.llm_allowed_tools() == {
        "finance_releves_search",
        "finance_releves_aggregate",
        "finance_bank_accounts_list",
    }


def test_llm_allowed_tools_parses_comma_separated(monkeypatch) -> None:
    monkeypatch.setenv(
        "AGENT_LLM_ALLOWED_TOOLS",
        " finance_releves_search, finance_transactions_sum ,,finance_bank_accounts_list ",
    )

    assert config.llm_allowed_tools() == {
        "finance_releves_search",
        "finance_transactions_sum",
        "finance_bank_accounts_list",
    }


def test_llm_fallback_enabled_requires_enabled_and_gated(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("AGENT_LLM_GATED", "true")

    assert config.llm_fallback_enabled() is True

    monkeypatch.setenv("AGENT_LLM_GATED", "false")
    assert config.llm_fallback_enabled() is False


def test_auto_resolve_merchant_aliases_enabled_defaults_true(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_AUTO_RESOLVE_MERCHANT_ALIASES", raising=False)

    assert config.auto_resolve_merchant_aliases_enabled() is True


def test_auto_resolve_merchant_aliases_limit_uses_default_on_invalid(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_AUTO_RESOLVE_MERCHANT_ALIASES_LIMIT", "invalid")

    assert config.auto_resolve_merchant_aliases_limit() == 50
