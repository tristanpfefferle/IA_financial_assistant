"""Deterministic bank detection from CSV headers and first rows."""

from __future__ import annotations

import unicodedata


BANK_SIGNATURES: dict[str, list[dict[str, list[str]]]] = {
    "revolut": [
        {
            "required_all": ["started date", "completed date", "amount", "currency"],
            "required_any": ["type", "product", "description", "fee", "state", "balance"],
        }
    ],
    "ubs": [
        {
            "required_all": ["booking date", "value date"],
            "required_any": ["transaction details", "debit", "credit"],
        },
        {
            "required_all": ["date de comptabilisation", "date valeur"],
            "required_any": ["details de transaction", "debit", "credit"],
        },
        {
            "required_all": ["buchungsdatum", "valutadatum"],
            "required_any": ["transaktionsdetails"],
        },
    ],
    "raiffeisen": [
        {
            "required_all": ["buchungsdatum", "valutadatum"],
            "required_any": ["mitteilung", "belastung", "gutschrift"],
        },
        {
            "required_all": ["date de comptabilisation", "date valeur"],
            "required_any": ["communication", "debit", "credit"],
        },
    ],
}


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip().lower()).encode("ascii", "ignore").decode("ascii")
    return " ".join(normalized.split())


def detect_bank_from_csv_bytes(data: bytes) -> str | None:
    """Return canonical bank code detected from CSV content, else ``None``."""

    if not data:
        return None

    text = data.decode("utf-8", errors="ignore")
    lines = [line for line in text.splitlines() if line.strip()][:10]
    if not lines:
        return None

    sample = _normalize_text("\n".join(lines))
    best_bank_code: str | None = None
    best_score = 0
    score_threshold = 2

    for bank_code, signatures in BANK_SIGNATURES.items():
        score = 0
        for signature in signatures:
            required_all = [_normalize_text(item) for item in signature.get("required_all", []) if item.strip()]
            required_any = [_normalize_text(item) for item in signature.get("required_any", []) if item.strip()]

            if required_all and all(item in sample for item in required_all):
                score += 2
            if required_any and any(item in sample for item in required_any):
                score += 1

        if score > best_score:
            best_bank_code = bank_code
            best_score = score

    if best_score < score_threshold:
        return None
    return best_bank_code
