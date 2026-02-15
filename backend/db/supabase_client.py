"""Minimal Supabase PostgREST client used by backend repositories only."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(slots=True)
class SupabaseSettings:
    url: str
    service_role_key: str


class SupabaseClient:
    def __init__(self, settings: SupabaseSettings) -> None:
        self.settings = settings

    def healthcheck(self) -> bool:
        return bool(self.settings.url and self.settings.service_role_key)

    def get_rows(
        self,
        *,
        table: str,
        query: dict[str, str | int],
        with_count: bool,
    ) -> tuple[list[dict[str, Any]], int | None]:
        """Fetch rows from PostgREST and optionally parse exact row count."""

        encoded_query = urlencode(query)
        request = Request(
            url=f"{self.settings.url}/rest/v1/{table}?{encoded_query}",
            headers={
                "apikey": self.settings.service_role_key,
                "Authorization": f"Bearer {self.settings.service_role_key}",
                "Accept": "application/json",
                "Prefer": "count=exact" if with_count else "return=representation",
            },
            method="GET",
        )
        with urlopen(request) as response:  # noqa: S310 - URL comes from trusted env config
            rows = json.loads(response.read().decode("utf-8"))
            total: int | None = None
            if with_count:
                content_range = response.headers.get("content-range")
                if content_range and "/" in content_range:
                    _, total_str = content_range.split("/", maxsplit=1)
                    total = int(total_str)
            return rows, total
