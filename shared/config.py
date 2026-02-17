"""Configuration helpers for environment variables."""

from __future__ import annotations

import os
import logging

from dotenv import load_dotenv


logger = logging.getLogger(__name__)


_TRUE_VALUES = {"1", "true"}


def _should_load_dotenv() -> bool:
    """Return whether local dotenv loading should run."""
    app_env = os.getenv("APP_ENV", "dev").strip().lower()
    return app_env in {"dev", "local"}


if _should_load_dotenv():
    load_dotenv()


def get_env(name: str, default: str | None = None) -> str | None:
    """Return a raw environment value or default."""
    return os.getenv(name, default)


def app_env() -> str:
    """Return the current application environment."""
    return (get_env("APP_ENV", "dev") or "dev").strip() or "dev"


def cors_allow_origins() -> list[str]:
    """Return CORS allowed origins from env with safe environment defaults."""
    raw_origins = get_env("CORS_ALLOW_ORIGINS", "") or ""
    parsed_origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]

    if parsed_origins:
        return parsed_origins

    if app_env().strip().lower() in {"dev", "local"}:
        return ["http://localhost:5173", "http://127.0.0.1:5173"]

    ui_origin = (get_env("UI_ORIGIN", "") or "").strip()
    if ui_origin:
        return [ui_origin]

    logger.warning(
        "cors_allow_origins_empty_in_prod app_env=%s; define CORS_ALLOW_ORIGINS or UI_ORIGIN",
        app_env(),
    )

    return []


def llm_enabled() -> bool:
    """Return whether the LLM planner is enabled."""
    if app_env().strip().lower() in {"test", "ci"}:
        return False

    raw_value = get_env("AGENT_LLM_ENABLED", "") or ""
    return raw_value.strip().lower() in _TRUE_VALUES


def llm_gated() -> bool:
    """Return whether gated LLM execution is enabled."""
    if app_env().strip().lower() in {"test", "ci"}:
        return False

    raw_value = get_env("AGENT_LLM_GATED", "") or ""
    return raw_value.strip().lower() in _TRUE_VALUES


def llm_allowed_tools() -> set[str]:
    """Return the allowlist of tool names that LLM can execute in gated mode."""
    raw_value = (get_env("AGENT_LLM_ALLOWED_TOOLS", "") or "").strip()
    if raw_value:
        return {
            tool_name.strip()
            for tool_name in raw_value.split(",")
            if tool_name.strip()
        }

    return {
        "finance_releves_search",
        "finance_releves_aggregate",
        "finance_bank_accounts_list",
    }


def llm_fallback_enabled() -> bool:
    """Return whether LLM fallback execution is globally enabled."""
    return llm_enabled() and llm_gated()


def llm_model() -> str:
    """Return configured LLM model with safe default."""
    return (get_env("AGENT_LLM_MODEL", "gpt-5") or "gpt-5").strip() or "gpt-5"


def llm_strict() -> bool:
    """Return whether strict LLM clarification behavior is enabled."""
    raw_value = get_env("AGENT_LLM_STRICT", "") or ""
    return raw_value.strip().lower() in _TRUE_VALUES


def llm_shadow() -> bool:
    """Return whether LLM shadow planning is enabled."""
    if app_env().strip().lower() in {"test", "ci"}:
        return False

    raw_value = get_env("AGENT_LLM_SHADOW", "") or ""
    return raw_value.strip().lower() in _TRUE_VALUES


def openai_api_key() -> str | None:
    """Return OpenAI API key when configured."""
    return get_env("OPENAI_API_KEY")


def supabase_url() -> str | None:
    """Return Supabase URL when configured."""
    return get_env("SUPABASE_URL")


def supabase_service_role_key() -> str | None:
    """Return Supabase service role key when configured."""
    return get_env("SUPABASE_SERVICE_ROLE_KEY")


def supabase_anon_key() -> str | None:
    """Return Supabase anon key when configured."""
    return get_env("SUPABASE_ANON_KEY")
