"""Configuration helpers for environment variables."""

from __future__ import annotations

import os

from dotenv import load_dotenv


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


def llm_enabled() -> bool:
    """Return whether the LLM planner is enabled."""
    if app_env().strip().lower() in {"test", "ci"}:
        return False

    raw_value = get_env("AGENT_LLM_ENABLED", "") or ""
    return raw_value.strip().lower() in {"1", "true"}


def llm_model() -> str:
    """Return configured LLM model with safe default."""
    return (get_env("AGENT_LLM_MODEL", "gpt-5") or "gpt-5").strip() or "gpt-5"


def openai_api_key() -> str | None:
    """Return OpenAI API key when configured."""
    return get_env("OPENAI_API_KEY")


def supabase_url() -> str | None:
    """Return Supabase URL when configured."""
    return get_env("SUPABASE_URL")


def supabase_service_role_key() -> str | None:
    """Return Supabase service role key when configured."""
    return get_env("SUPABASE_SERVICE_ROLE_KEY")
