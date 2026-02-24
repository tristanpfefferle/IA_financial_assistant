"""Multi-bank releves import pipeline."""

from __future__ import annotations

__all__ = ["RelevesImportService"]


def __getattr__(name: str):
    if name == "RelevesImportService":
        from backend.services.releves_import.importer import RelevesImportService

        return RelevesImportService
    raise AttributeError(name)
