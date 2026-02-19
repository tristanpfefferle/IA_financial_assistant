"""Deterministic Swiss bank catalog and message extraction helpers."""

from __future__ import annotations

import re
import unicodedata
import string


def normalize(text: str) -> str:
    """Normalize text for deterministic lookup matching."""

    ascii_text = (
        unicodedata.normalize("NFKD", text)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
        .strip()
    )
    collapsed = re.sub(r"\s+", " ", ascii_text)
    return collapsed.strip(string.punctuation + " ")


_BANK_VARIANTS: dict[str, list[str]] = {
    "UBS": ["ubs", "ubs suisse", "union de banques suisses"],
    "Raiffeisen": ["raiffeisen", "raifeisen", "raiffeisen suisse"],
    "PostFinance": ["postfinance", "post finance", "la poste finance"],
    "Zürcher Kantonalbank": ["zkb", "zuercher kantonalbank", "zurich cantonal bank"],
    "Banque Cantonale Vaudoise": ["bcv", "banque cantonale vaudoise"],
    "Banque Cantonale de Genève": ["bcge", "banque cantonale de geneve"],
    "Banque Migros": ["banque migros", "migros bank", "migros"],
    "Banque Cler": ["banque cler", "cler"],
    "Julius Baer": ["julius baer", "baer"],
    "Credit Suisse (legacy)": ["credit suisse", "cs"],
    "Revolut": ["revolut"],
    "Wise": ["wise", "transferwise"],
}

BANK_ALIASES: dict[str, str] = {
    normalize(alias): canonical
    for canonical, aliases in _BANK_VARIANTS.items()
    for alias in aliases
}


def extract_canonical_banks(message: str) -> tuple[list[str], list[str]]:
    """Extract canonical bank names and unknown segments from a user message."""

    segments = re.split(r",|\s+et\s+|&|/", message, flags=re.IGNORECASE)
    matched: list[str] = []
    unknown: list[str] = []
    seen_matched: set[str] = set()
    seen_unknown: set[str] = set()

    for raw_segment in segments:
        segment = raw_segment.strip()
        if not segment:
            continue
        normalized_segment = normalize(segment)
        if not normalized_segment:
            continue

        canonical = BANK_ALIASES.get(normalized_segment)
        if canonical is not None:
            key = canonical.lower()
            if key not in seen_matched:
                seen_matched.add(key)
                matched.append(canonical)
            continue

        if normalized_segment not in seen_unknown:
            seen_unknown.add(normalized_segment)
            unknown.append(segment)

    return matched, unknown
