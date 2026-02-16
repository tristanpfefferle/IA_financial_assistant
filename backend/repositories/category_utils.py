"""Utilities for category name handling in repositories."""

from __future__ import annotations


def normalize_category_name(s: str) -> str:
    """Normalize category names for reliable comparisons."""
    return " ".join(s.strip().lower().split())

