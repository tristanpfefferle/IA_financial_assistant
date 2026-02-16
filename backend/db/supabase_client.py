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


class SupabaseClient:
    def __init__(self, settings: SupabaseSettings) -> None:
        self.settings = settings

    def healthcheck(self) -> bool:
        return bool(self.settings.url and self.settings.service_role_key)

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
            body = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(
                f"Supabase request failed with status {exc.code}: {body}"
            ) from exc
