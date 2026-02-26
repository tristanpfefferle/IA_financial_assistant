"""Tests for backend service factory behavior."""

from __future__ import annotations

import pytest

from backend.factory import build_backend_tool_service


def test_build_backend_tool_service_raises_in_prod_without_supabase(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse in-memory repository fallback outside local/test environments."""

    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

    with pytest.raises(RuntimeError, match="Supabase not configured"):
        build_backend_tool_service()
