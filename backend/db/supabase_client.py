"""Minimal Supabase PostgREST client used by backend repositories only."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(slots=True)
class SupabaseSettings:
    url: str
    service_role_key: str
    anon_key: str | None = None


class SupabaseRequestError(RuntimeError):
    """Structured Supabase HTTP failure with parsed payload when available."""

    def __init__(
        self,
        *,
        status_code: int,
        error_json: dict[str, Any] | None,
        raw_text: str | None,
    ) -> None:
        self.status_code = status_code
        self.error_json = error_json
        self.raw_text = raw_text

        detail = error_json if error_json is not None else raw_text
        super().__init__(f"Supabase request failed with status {status_code}: {detail}")


class SupabaseClient:
    def __init__(self, settings: SupabaseSettings) -> None:
        self.settings = settings

    def healthcheck(self) -> bool:
        return bool(self.settings.url and self.settings.service_role_key)

    @staticmethod
    def _raise_http_error(exc: HTTPError) -> None:
        body = exc.read().decode("utf-8", errors="replace")
        error_json: dict[str, Any] | None = None
        raw_text = body[:3000] if body else None
        if body:
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                error_json = parsed
        raise SupabaseRequestError(
            status_code=exc.code,
            error_json=error_json,
            raw_text=raw_text,
        ) from exc


    def patch_rows(
        self,
        *,
        table: str,
        query: dict[str, str | int] | list[tuple[str, str | int]],
        payload: dict[str, Any],
        use_anon_key: bool = False,
    ) -> list[dict[str, Any]]:
        """Patch rows in PostgREST and return representation."""

        encoded_query = urlencode(query, doseq=True)
        api_key = self.settings.anon_key if use_anon_key else self.settings.service_role_key
        if not api_key:
            raise ValueError("Missing Supabase API key for requested mode")
        request = Request(
            url=f"{self.settings.url}/rest/v1/{table}?{encoded_query}",
            headers={
                "apikey": api_key,
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
            data=json.dumps(payload).encode("utf-8"),
            method="PATCH",
        )
        try:
            with urlopen(request) as response:  # noqa: S310 - URL comes from trusted env config
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            self._raise_http_error(exc)

    def post_rows(
        self,
        *,
        table: str,
        payload: dict[str, Any] | list[dict[str, Any]],
        use_anon_key: bool = False,
        prefer: str = "return=representation",
    ) -> list[dict[str, Any]]:
        """Insert rows in PostgREST and return representation."""

        api_key = self.settings.anon_key if use_anon_key else self.settings.service_role_key
        if not api_key:
            raise ValueError("Missing Supabase API key for requested mode")
        request = Request(
            url=f"{self.settings.url}/rest/v1/{table}",
            headers={
                "apikey": api_key,
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Prefer": prefer,
            },
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
        )
        try:
            with urlopen(request) as response:  # noqa: S310 - URL comes from trusted env config
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            self._raise_http_error(exc)

    def delete_rows(
        self,
        *,
        table: str,
        query: dict[str, str] | list[tuple[str, str]],
        use_anon_key: bool = False,
    ) -> list[dict[str, Any]]:
        """Delete rows in PostgREST and return representation."""

        encoded_query = urlencode(query, doseq=True)
        api_key = self.settings.anon_key if use_anon_key else self.settings.service_role_key
        if not api_key:
            raise ValueError("Missing Supabase API key for requested mode")
        request = Request(
            url=f"{self.settings.url}/rest/v1/{table}?{encoded_query}",
            headers={
                "apikey": api_key,
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "Prefer": "return=representation",
            },
            method="DELETE",
        )
        try:
            with urlopen(request) as response:  # noqa: S310 - URL comes from trusted env config
                content = response.read().decode("utf-8")
                return json.loads(content) if content else []
        except HTTPError as exc:
            self._raise_http_error(exc)

    def upsert_row(
        self,
        *,
        table: str,
        payload: dict[str, Any],
        on_conflict: str,
        use_anon_key: bool = False,
    ) -> list[dict[str, Any]]:
        """Upsert one row in PostgREST and return representation."""

        api_key = self.settings.anon_key if use_anon_key else self.settings.service_role_key
        if not api_key:
            raise ValueError("Missing Supabase API key for requested mode")

        encoded_query = urlencode({"on_conflict": on_conflict})
        request = Request(
            url=f"{self.settings.url}/rest/v1/{table}?{encoded_query}",
            headers={
                "apikey": api_key,
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=representation",
            },
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
        )
        try:
            with urlopen(request) as response:  # noqa: S310 - URL comes from trusted env config
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            self._raise_http_error(exc)

    def get_rows(
        self,
        *,
        table: str,
        query: dict[str, str | int] | list[tuple[str, str | int]],
        with_count: bool,
        use_anon_key: bool = False,
    ) -> tuple[list[dict[str, Any]], int | None]:
        """Fetch rows from PostgREST and optionally parse exact row count."""

        encoded_query = urlencode(query, doseq=True)
        api_key = self.settings.anon_key if use_anon_key else self.settings.service_role_key
        if not api_key:
            raise ValueError("Missing Supabase API key for requested mode")
        request = Request(
            url=f"{self.settings.url}/rest/v1/{table}?{encoded_query}",
            headers={
                "apikey": api_key,
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "Prefer": "count=exact" if with_count else "return=representation",
            },
            method="GET",
        )
        try:
            with urlopen(request) as response:  # noqa: S310 - URL comes from trusted env config
                rows = json.loads(response.read().decode("utf-8"))
                total: int | None = None
                if with_count:
                    content_range = response.headers.get("content-range")
                    if content_range and "/" in content_range:
                        _, total_str = content_range.split("/", maxsplit=1)
                        total = int(total_str)
                return rows, total
        except HTTPError as exc:
            self._raise_http_error(exc)
