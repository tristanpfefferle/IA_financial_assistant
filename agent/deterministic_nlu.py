"""Deterministic natural-language intent parser for core finance actions."""

from __future__ import annotations

import calendar
import re
from datetime import date

_FRENCH_MONTHS: dict[str, int] = {
    "janvier": 1,
    "janv": 1,
    "janv.": 1,
    "fevrier": 2,
    "février": 2,
    "fevr": 2,
    "févr": 2,
    "fevr.": 2,
    "févr.": 2,
    "mars": 3,
    "avril": 4,
    "avr": 4,
    "avr.": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "juil": 7,
    "juil.": 7,
    "aout": 8,
    "août": 8,
    "septembre": 9,
    "sept": 9,
    "sept.": 9,
    "octobre": 10,
    "oct": 10,
    "oct.": 10,
    "novembre": 11,
    "nov": 11,
    "nov.": 11,
    "decembre": 12,
    "décembre": 12,
    "dec": 12,
    "déc": 12,
    "dec.": 12,
    "déc.": 12,
}

_CREATE_ACCOUNT_PATTERNS = (
    re.compile(r"(?:cr[ée]e?r?|ajoute)\s+(?:un\s+)?compte(?:\s+bancaire)?(?:\s+nomm[ée])?\s*(?P<name>.+)?$", re.IGNORECASE),
    re.compile(r"nouveau\s+compte\s*:\s*(?P<name>.+)?$", re.IGNORECASE),
)

_LIST_ACCOUNTS_PATTERNS = (
    re.compile(r"liste\s+mes\s+comptes", re.IGNORECASE),
    re.compile(r"quels\s+sont\s+mes\s+comptes(?:\s+bancaires)?", re.IGNORECASE),
    re.compile(r"affiche\s+mes\s+comptes", re.IGNORECASE),
)

_IMPORT_PATTERNS = (
    re.compile(r"\bimporter\b.*\b(relev[ée]|csv)\b", re.IGNORECASE),
    re.compile(r"\bje\s+veux\s+importer\b", re.IGNORECASE),
    re.compile(r"\bajouter?\s+un\s+relev[ée]\b", re.IGNORECASE),
)

_SEARCH_PREFIXES = (
    "cherche",
    "recherche",
    "montre moi les transactions",
    "montre-moi les transactions",
    "montre les transactions",
)


def _strip_terminal_punctuation(value: str) -> str:
    return value.strip().strip(" .,!?:;\"'“”«»")


def _normalize_message_for_match(value: str) -> str:
    stripped = _strip_terminal_punctuation(value)
    return re.sub(r"\s+", " ", stripped)


def _extract_date_range_from_message(message: str) -> tuple[dict[str, date], str] | None:
    lower = message.lower()
    month_tokens = sorted(_FRENCH_MONTHS.keys(), key=len, reverse=True)
    month_pattern = "|".join(re.escape(token) for token in month_tokens)
    match = re.search(
        rf"\ben\s+(?P<month>{month_pattern})(?:\s+(?P<year>19\d{{2}}|20\d{{2}}|21\d{{2}}))?\b",
        lower,
    )
    if match is None:
        return None

    month_token = match.group("month")
    month = _FRENCH_MONTHS.get(month_token)
    if month is None:
        return None

    year_text = match.group("year")
    year = int(year_text) if year_text else date.today().year
    last_day = calendar.monthrange(year, month)[1]
    date_range = {
        "start_date": date(year, month, 1),
        "end_date": date(year, month, last_day),
    }

    cleaned = f"{lower[:match.start()]} {lower[match.end():]}"
    return date_range, _strip_terminal_punctuation(cleaned)


def _extract_search_term(message: str) -> tuple[str | None, dict[str, date] | None]:
    lowered = message.strip().lower()
    remainder = lowered
    for prefix in _SEARCH_PREFIXES:
        if lowered.startswith(prefix):
            remainder = lowered[len(prefix) :].strip()
            break

    date_range: dict[str, date] | None = None
    extracted = _extract_date_range_from_message(remainder)
    if extracted is not None:
        date_range, remainder = extracted

    if remainder.startswith("de "):
        remainder = remainder[3:]
    if remainder.startswith("des "):
        remainder = remainder[4:]

    term = _strip_terminal_punctuation(remainder)
    return term or None, date_range


def parse_intent(message: str) -> dict[str, object] | None:
    """Parse deterministic intents from a user message."""

    normalized = message.strip()
    if not normalized:
        return None

    lower = normalized.lower()
    normalized_for_match = _normalize_message_for_match(normalized)

    for pattern in _CREATE_ACCOUNT_PATTERNS:
        match = pattern.search(normalized)
        if match is None:
            continue
        account_name = _strip_terminal_punctuation(match.group("name") or "")
        if not account_name:
            return {
                "type": "clarification",
                "message": "Quel nom voulez-vous donner au compte bancaire ?",
            }
        return {
            "type": "tool_call",
            "tool_name": "finance_bank_accounts_create",
            "payload": {"name": account_name},
        }

    if any(pattern.fullmatch(normalized_for_match) for pattern in _LIST_ACCOUNTS_PATTERNS):
        return {
            "type": "tool_call",
            "tool_name": "finance_bank_accounts_list",
            "payload": {},
        }

    if any(pattern.search(normalized) for pattern in _IMPORT_PATTERNS):
        return {
            "type": "ui_action",
            "action": "open_import_panel",
        }

    if lower.startswith(_SEARCH_PREFIXES):
        merchant, date_range = _extract_search_term(normalized)
        if not merchant:
            return {
                "type": "clarification",
                "message": "Que voulez-vous rechercher (ex: Migros, coffee, Coop) ?",
            }

        payload: dict[str, object] = {"merchant": merchant, "limit": 50, "offset": 0}
        if date_range is not None:
            payload["date_range"] = date_range
        return {
            "type": "tool_call",
            "tool_name": "finance_releves_search",
            "payload": payload,
        }

    return None
