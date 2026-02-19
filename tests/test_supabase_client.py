"""Unit tests for Supabase client query encoding and error normalization."""

from __future__ import annotations

from io import BytesIO
from urllib.error import HTTPError

import pytest

from backend.db.supabase_client import SupabaseClient, SupabaseSettings


def _build_client() -> SupabaseClient:
    return SupabaseClient(
        SupabaseSettings(url="https://example.supabase.co", service_role_key="service-role")
    )


def test_get_rows_uses_doseq_for_repeated_query_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _build_client()

    def _fake_urlopen(request):
        assert "date=gte.2025-01-01" in request.full_url
        assert "date=lte.2025-01-31" in request.full_url

        class _Response:
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return b"[]"

        return _Response()

    monkeypatch.setattr("backend.db.supabase_client.urlopen", _fake_urlopen)

    rows, total = client.get_rows(
        table="releves_bancaires",
        query=[("date", "gte.2025-01-01"), ("date", "lte.2025-01-31")],
        with_count=False,
    )

    assert rows == []
    assert total is None


def test_get_rows_includes_status_and_body_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _build_client()

    def _raise_http_error(_request):
        raise HTTPError(
            url="https://example.supabase.co/rest/v1/releves_bancaires",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=BytesIO(b"Bad Request from Supabase"),
        )

    monkeypatch.setattr("backend.db.supabase_client.urlopen", _raise_http_error)

    with pytest.raises(RuntimeError, match="status 400") as error:
        client.get_rows(table="releves_bancaires", query={"select": "*"}, with_count=False)

    assert "Bad Request from Supabase" in str(error.value)


def test_post_rows_sets_prefer_header(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _build_client()

    def _fake_urlopen(request):
        assert request.get_method() == "POST"
        assert request.full_url == "https://example.supabase.co/rest/v1/chat_state"
        assert request.get_header("Prefer") == "resolution=merge-duplicates,return=representation"

        class _Response:
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return b"[]"

        return _Response()

    monkeypatch.setattr("backend.db.supabase_client.urlopen", _fake_urlopen)

    rows = client.post_rows(
        table="chat_state",
        payload={"conversation_id": "abc"},
        prefer="resolution=merge-duplicates,return=representation",
    )

    assert rows == []


def test_upsert_row_sets_on_conflict_and_prefer_header(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _build_client()

    def _fake_urlopen(request):
        assert request.get_method() == "POST"
        assert request.full_url == "https://example.supabase.co/rest/v1/chat_state?on_conflict=conversation_id"
        assert request.get_header("Prefer") == "resolution=merge-duplicates,return=representation"

        class _Response:
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return b"[]"

        return _Response()

    monkeypatch.setattr("backend.db.supabase_client.urlopen", _fake_urlopen)

    rows = client.upsert_row(
        table="chat_state",
        payload={"conversation_id": "abc", "active_task": None},
        on_conflict="conversation_id",
    )

    assert rows == []


def test_delete_rows_uses_delete_method_and_query_params(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _build_client()

    def _fake_urlopen(request):
        assert request.get_method() == "DELETE"
        assert request.full_url == (
            "https://example.supabase.co/rest/v1/releves_bancaires?"
            "profile_id=eq.00000000-0000-0000-0000-000000000000"
        )

        class _Response:
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return b"[]"

        return _Response()

    monkeypatch.setattr("backend.db.supabase_client.urlopen", _fake_urlopen)

    rows = client.delete_rows(
        table="releves_bancaires",
        query={"profile_id": "eq.00000000-0000-0000-0000-000000000000"},
    )

    assert rows == []
