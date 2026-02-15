"""Supabase client placeholder.

The concrete implementation will be the only DB entrypoint and will support wrappers
around `gestion_financiere` repository functions.
"""

from dataclasses import dataclass


@dataclass(slots=True)
class SupabaseSettings:
    url: str
    service_role_key: str


class SupabaseClient:
    def __init__(self, settings: SupabaseSettings) -> None:
        self.settings = settings

    def healthcheck(self) -> bool:
        return bool(self.settings.url and self.settings.service_role_key)
