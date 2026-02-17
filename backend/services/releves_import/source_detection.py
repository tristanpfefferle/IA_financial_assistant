"""Source detection adapted from legacy gestion_financiere.core.detection_source."""

from __future__ import annotations

import unicodedata


def _normalize(text: str) -> str:
    if not isinstance(text, str):
        return ""
    normalized = text.replace("\ufeff", "")
    normalized = unicodedata.normalize("NFKC", normalized)
    return "".join(char for char in normalized if char.isprintable() or char in "\n\t")


def detect_source(filename: str, content: bytes) -> str:
    name = filename.lower()
    signature = "unknown"

    if name.endswith(".pdf"):
        return "unknown"

    if not name.endswith(".csv"):
        return "unsupported_format"

    try:
        content_str = content.decode("utf-8", errors="ignore")
        content_str = _normalize(content_str)
    except Exception:
        return "csv_error"

    lines = content_str.splitlines()
    text = "\n".join(line.lower() for line in lines[:10])

    if name.startswith("konto_"):
        signature = "raiffeisen"

    if len(lines) >= 4:
        first = lines[0].strip().lower()
        second = lines[1].strip().lower()
        third = lines[2].strip().lower()
        fourth = lines[3].strip().lower()
        if (
            first.startswith("numéro de compte:")
            and second.startswith("iban:")
            and third.startswith("du:")
            and fourth.startswith("au:")
        ):
            signature = "ubs"

    if lines:
        header = lines[0].strip().lower()
        if "valuta" in header and "belastung chf" in header:
            signature = "raiffeisen"

    if "interactive brokers" in text:
        signature = "interactivebrokers"
    elif "ubs switzerland" in text or "ubs ag" in text:
        signature = "ubs"
    elif "booking date" in text and "value date" in text and "balance" in text:
        signature = "ubs"
    elif "date de valeur" in text and "solde" in text and "devise" in text:
        signature = "ubs"
    elif "raiffeisen" in text:
        signature = "raiffeisen"
    elif "degiro" in text:
        signature = "degiro"
    elif "swissquote" in text:
        signature = "swissquote"
    elif "revolut" in text:
        signature = "revolut"

    return signature
