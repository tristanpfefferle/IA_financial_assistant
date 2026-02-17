"""Interactive Brokers parser placeholder (investment export, not releves_bancaires)."""

from __future__ import annotations


def parse_interactive_brokers_csv(file_bytes: bytes) -> list[dict[str, object]]:
    """Return no releves rows (legacy flow stores this data in investment tables)."""

    _ = file_bytes
    return []
