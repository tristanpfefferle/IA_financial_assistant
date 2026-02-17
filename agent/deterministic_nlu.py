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
    re.compile(
        r"(?:cr[ée]e?r?|ajoute)\s+(?:un\s+)?compte(?:\s+bancaire)?(?:\s+nomm[ée])?\s*(?P<name>.+)?$",
        re.IGNORECASE,
    ),
    re.compile(r"nouveau\s+compte\s*:\s*(?P<name>.+)?$", re.IGNORECASE),
)

_LIST_ACCOUNTS_PATTERNS = (
    re.compile(r"liste\s+mes\s+comptes", re.IGNORECASE),
    re.compile(r"quels\s+sont\s+mes\s+comptes(?:\s+bancaires)?", re.IGNORECASE),
    re.compile(r"affiche\s+mes\s+comptes", re.IGNORECASE),
    re.compile(r"montre(?:-?\s?moi)?\s+mes\s+comptes(?:\s+bancaires)?", re.IGNORECASE),
    re.compile(r"j['’]ai\s+combien\s+de\s+comptes\s+bancaires", re.IGNORECASE),
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

_BANK_ACCOUNT_HINTS = {
    "ubs",
    "revolut",
    "neon",
    "postfinance",
    "raiffeisen",
    "cs",
    "credit suisse",
    "crédit suisse",
    "credit-suisse",
    "crédit-suisse",
}

_BANK_HINT_SUFFIXES = {
    "pro",
}


def _strip_terminal_punctuation(value: str) -> str:
    return value.strip().strip(" .,!?:;\"'“”«»")


def _normalize_message_for_match(value: str) -> str:
    stripped = _strip_terminal_punctuation(value)
    return re.sub(r"\s+", " ", stripped).strip()


def _extract_date_range_from_message(
    message: str,
) -> tuple[dict[str, date], str] | None:
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


def parse_search_query_parts(message: str) -> dict[str, object]:
    """Split a search message into merchant text, optional bank hint, and date range."""

    merchant_text, date_range = _extract_search_term(message)
    if merchant_text is None:
        return {
            "merchant_text": "",
            "bank_account_hint": None,
            "date_range": date_range,
        }

    merchant_fallback = merchant_text
    tokens = [token for token in merchant_text.split() if token]
    if len(tokens) < 2:
        return {
            "merchant_text": merchant_text,
            "bank_account_hint": None,
            "date_range": date_range,
        }

    normalized_tokens = [
        _strip_terminal_punctuation(token).casefold() for token in tokens
    ]

    suffix_consumed = 0
    if normalized_tokens[-1] in _BANK_HINT_SUFFIXES:
        suffix_consumed = 1

    hint_token_count = 0
    candidate: str | None = None
    max_hint_span = len(normalized_tokens) - suffix_consumed
    if max_hint_span >= 2:
        two_token_candidate = " ".join(
            normalized_tokens[-(2 + suffix_consumed) : -suffix_consumed or None]
        ).strip()
        if two_token_candidate in _BANK_ACCOUNT_HINTS:
            candidate = two_token_candidate
            hint_token_count = 2

    if candidate is None and max_hint_span >= 1:
        one_token_candidate = normalized_tokens[-(1 + suffix_consumed)]
        if one_token_candidate in _BANK_ACCOUNT_HINTS:
            candidate = one_token_candidate
            hint_token_count = 1

    if candidate is not None:
        consumed_token_count = hint_token_count + suffix_consumed
        cleaned_merchant = " ".join(tokens[:-consumed_token_count]).strip()
        return {
            "merchant_text": cleaned_merchant,
            "bank_account_hint": candidate,
            "date_range": date_range,
            "merchant_fallback": merchant_fallback,
        }

    return {
        "merchant_text": merchant_text,
        "bank_account_hint": None,
        "date_range": date_range,
    }


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

    if any(
        pattern.fullmatch(normalized_for_match) for pattern in _LIST_ACCOUNTS_PATTERNS
    ):
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
        parts = parse_search_query_parts(normalized)
        merchant = parts.get("merchant_text")
        bank_account_hint = parts.get("bank_account_hint")
        date_range = parts.get("date_range")
        if not merchant:
            clarification_payload: dict[str, object] = {
                "type": "clarification",
                "message": "Que voulez-vous rechercher (ex: Migros, coffee, Coop) ?",
                "clarification_type": "awaiting_search_merchant",
            }
            if date_range is not None:
                clarification_payload["date_range"] = date_range
            return clarification_payload

        payload: dict[str, object] = {"merchant": merchant, "limit": 50, "offset": 0}
        if date_range is not None:
            payload["date_range"] = date_range
        tool_call_intent: dict[str, object] = {
            "type": "tool_call",
            "tool_name": "finance_releves_search",
            "payload": payload,
        }
        if isinstance(bank_account_hint, str) and bank_account_hint:
            tool_call_intent["bank_account_hint"] = bank_account_hint
            merchant_fallback = parts.get("merchant_fallback")
            if isinstance(merchant_fallback, str) and merchant_fallback.strip():
                tool_call_intent["merchant_fallback"] = merchant_fallback
        return tool_call_intent

    return None
