"""Confidence parsing helpers for onboarding profile collection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
import re
from typing import Any


class ConfidenceLevel(str, Enum):
    """Supported confidence levels for deterministic extraction."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(slots=True)
class ParsedValue:
    """Parsed value with confidence metadata."""

    value: Any | None
    confidence: ConfidenceLevel
    reasons: list[str]


_ALPHA_TOKEN_PATTERN = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'\-]*")
_ISO_DATE_PATTERN = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DOT_DATE_PATTERN = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
_SLASH_DATE_PATTERN = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_CONDITIONAL_CONNECTORS = ("mais", "sauf", "uniquement", "et au fait")
_NON_NAME_TOKENS = {"bonjour", "salut", "hello", "coucou", "pernom", "prenom", "nom"}


def _to_iso(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _extract_birth_date(message: str) -> tuple[str | None, list[str]]:
    for pattern, converter in (
        (_ISO_DATE_PATTERN, lambda m: _to_iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))),
        (_DOT_DATE_PATTERN, lambda m: _to_iso(int(m.group(3)), int(m.group(2)), int(m.group(1)))),
        (_SLASH_DATE_PATTERN, lambda m: _to_iso(int(m.group(3)), int(m.group(2)), int(m.group(1)))),
    ):
        match = pattern.search(message)
        if match is None:
            continue
        iso = converter(match)
        if iso is not None:
            return iso, ["explicit_birth_date"]
    return None, []


def parse_profile_collect_message(message: str) -> dict[str, ParsedValue]:
    """Extract profile fields with deterministic confidence metadata."""

    stripped = message.strip()
    lowered = stripped.lower()
    is_long = len(stripped) > 120
    contains_conditions = any(connector in lowered for connector in _CONDITIONAL_CONNECTORS)

    birth_date, birth_reasons = _extract_birth_date(stripped)
    name_source = stripped
    if birth_date is not None:
        name_source = _ISO_DATE_PATTERN.sub(" ", name_source)
        name_source = _DOT_DATE_PATTERN.sub(" ", name_source)
        name_source = _SLASH_DATE_PATTERN.sub(" ", name_source)

    tokens = _ALPHA_TOKEN_PATTERN.findall(name_source)

    first_name_value = tokens[0] if tokens else None
    last_name_value = tokens[1] if len(tokens) > 1 else None

    first_reasons: list[str] = []
    last_reasons: list[str] = []

    first_confidence = ConfidenceLevel.LOW
    last_confidence = ConfidenceLevel.LOW

    if len(tokens) == 1:
        token_lower = tokens[0].lower()
        if token_lower in _NON_NAME_TOKENS:
            first_confidence = ConfidenceLevel.LOW
            first_reasons.append("non_name_token")
        else:
            first_confidence = ConfidenceLevel.HIGH
            first_reasons.append("single_token_name")
    elif len(tokens) >= 2:
        first_confidence = ConfidenceLevel.MEDIUM
        last_confidence = ConfidenceLevel.MEDIUM
        first_reasons.append("ambiguous_multi_token")
        last_reasons.append("ambiguous_multi_token")

    if birth_date is not None and len(tokens) >= 2:
        first_confidence = ConfidenceLevel.HIGH
        last_confidence = ConfidenceLevel.HIGH
        first_reasons.append("name_with_explicit_birth_date")
        last_reasons.append("name_with_explicit_birth_date")

    if is_long:
        if first_confidence != ConfidenceLevel.HIGH:
            first_confidence = ConfidenceLevel.LOW
        if last_confidence != ConfidenceLevel.HIGH:
            last_confidence = ConfidenceLevel.LOW
        first_reasons.append("message_long")
        last_reasons.append("message_long")
        birth_reasons.append("message_long")

    if contains_conditions:
        if first_confidence != ConfidenceLevel.HIGH:
            first_confidence = ConfidenceLevel.LOW
        if last_confidence != ConfidenceLevel.HIGH:
            last_confidence = ConfidenceLevel.LOW
        first_reasons.append("contains_conditions")
        last_reasons.append("contains_conditions")
        birth_reasons.append("contains_conditions")

    birth_confidence = ConfidenceLevel.HIGH if birth_date is not None else ConfidenceLevel.LOW
    if birth_date is None and (is_long or contains_conditions):
        birth_reasons.append("contains_conditions" if contains_conditions else "message_long")

    return {
        "first_name": ParsedValue(value=first_name_value, confidence=first_confidence, reasons=list(dict.fromkeys(first_reasons))),
        "last_name": ParsedValue(value=last_name_value, confidence=last_confidence, reasons=list(dict.fromkeys(last_reasons))),
        "birth_date": ParsedValue(value=birth_date, confidence=birth_confidence, reasons=list(dict.fromkeys(birth_reasons))),
    }
