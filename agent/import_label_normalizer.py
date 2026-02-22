"""Utilities for deterministic import label normalization."""

from __future__ import annotations

import re

_MAX_ALIAS_LENGTH = 140
_NO_TRANSACTION_PATTERN = re.compile(r"\bno\s+de\s+transaction\s*:\s*", re.IGNORECASE)
_COOP_PATTERN = re.compile(r"^coop\s*-\s*\d+", re.IGNORECASE)
_IBAN_PATTERN = re.compile(r"\bCH\d{2}[A-Z0-9]{17}\b")
_LONG_NUMERIC_PATTERN = re.compile(r"\b\d{6,}\b")
_REFERENCE_PATTERN = re.compile(r"\b(?:qrr\s*:|reference\s+no\.?\s*:?)\s*[a-z0-9-]+", re.IGNORECASE)
_MERCHANT_SIGNAL_PATTERN = re.compile(r"\b(?:sumup|paypal|stripe|worldline|adyen)\b", re.IGNORECASE)


def _collapse_spaces(value: str) -> str:
    return " ".join(value.split())


def _truncate_alias(value: str, *, max_length: int = _MAX_ALIAS_LENGTH) -> str:
    """Truncate to a stable short alias while keeping useful word boundaries."""

    if len(value) <= max_length:
        return value

    slice_size = max(1, max_length - 1)
    head = value[:slice_size].rstrip()
    if " " in head:
        head = head.rsplit(" ", 1)[0].rstrip()
    if not head:
        head = value[:slice_size].rstrip()
    return f"{head}…"


def extract_observed_alias_from_label(label: str | None) -> str | None:
    """Extract a short and stable observed alias from an imported UBS label."""

    if not isinstance(label, str):
        return None

    raw_label = _collapse_spaces(label)
    if not raw_label:
        return None

    head = raw_label.replace(";", " ").strip()
    if not head:
        return None

    head_lower = head.lower()
    if head.startswith("XXXX") and "paiement à une carte" not in head_lower:
        return "Paiement à une carte"
    if "virement compte à compte" in head_lower:
        return "Virement interne"

    no_transaction_match = _NO_TRANSACTION_PATTERN.search(head)
    if no_transaction_match:
        head = head[: no_transaction_match.start()].strip()

    head = _collapse_spaces(head)
    if not head:
        return None

    if head.lower().startswith("coop pronto"):
        return "Coop Pronto"
    if _COOP_PATTERN.match(head):
        return "Coop"

    head_upper = head.upper()
    if "SBB" in head_upper and "MOBILE" in head_upper:
        return "SBB Mobile"

    if head.startswith("XXXX") and "paiement à une carte" in head_lower and not _MERCHANT_SIGNAL_PATTERN.search(head):
        return "Paiement à une carte"

    if "twint" in head.lower() and not re.search(r"\btwint\b", head, flags=re.IGNORECASE):
        head = f"TWINT {head}"

    merchant_signal_match = _MERCHANT_SIGNAL_PATTERN.search(head)
    if merchant_signal_match:
        head = head[merchant_signal_match.start() :]

    head = _REFERENCE_PATTERN.sub("[REF]", head)
    head = _IBAN_PATTERN.sub("[IBAN]", head)
    head = _LONG_NUMERIC_PATTERN.sub("[NUM]", head)
    head = _collapse_spaces(head)

    if not head:
        return None

    return _truncate_alias(head, max_length=_MAX_ALIAS_LENGTH)
