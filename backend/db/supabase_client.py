"""Supabase client placeholder.

Real queries must be implemented here or in dedicated repositories.
"""

from dataclasses import dataclass


@dataclass(slots=True)
class SupabaseClient:
    """Minimal config container for future Supabase integration."""

    url: str
    key: str

    def is_configured(self) -> bool:
        """Return True when both URL and KEY are provided."""
        return bool(self.url and self.key)
