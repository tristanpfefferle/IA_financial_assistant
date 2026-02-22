"""UBS parser adapted from legacy extraction_ubs.py."""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from decimal import Decimal
from typing import Any


_EXCLUDED_LABEL_KEYWORDS = (
    "date",
    "debit",
    "débit",
    "credit",
    "crédit",
    "montant",
    "amount",
    "monnaie",
    "currency",
    "solde",
    "balance",
    "numéro de compte",
    "account",
    "iban",
    "valeur",
)
_PREFERRED_TEXT_KEYWORDS = (
    "description",
    "motif",
    "reference",
    "référence",
    "texte",
    "information",
)


def _parse_date(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return text or None


def _parse_amount(value: str | float | None, debit_credit: str | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        amount = Decimal(str(value))
    else:
        cleaned = re.sub(r"[^0-9,\.-]", "", value).replace(",", ".")
        if not cleaned:
            return None
        try:
            amount = Decimal(cleaned)
        except Exception:
            return None

    dc = (debit_credit or "").strip().lower()
    if dc in {"débit", "debit", "d", "soll"}:
        return -abs(amount)
    if dc in {"crédit", "credit", "c", "haben"}:
        return abs(amount)
    return amount


def _is_textual_value(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    return bool(stripped and not re.fullmatch(r"[0-9\s.,'-]+", stripped))


def _build_ubs_label(raw: dict[str, Any]) -> str | None:
    fragments: list[str] = []
    seen: set[str] = set()

    def _append_fragment(value: str | None) -> None:
        if not value:
            return
        fragment = " ".join(value.split())
        if not fragment:
            return
        normalized = fragment.casefold()
        if normalized in seen:
            return
        seen.add(normalized)
        fragments.append(fragment)

    for key in ("Description1", "Description2", "Description3"):
        _append_fragment(raw.get(key))

    for key, value in raw.items():
        key_text = (key or "").strip()
        key_lower = key_text.casefold()
        if key_text in {"Description1", "Description2", "Description3"}:
            continue
        if any(keyword in key_lower for keyword in _EXCLUDED_LABEL_KEYWORDS):
            continue
        if not any(keyword in key_lower for keyword in _PREFERRED_TEXT_KEYWORDS):
            continue
        if not _is_textual_value(value):
            continue
        _append_fragment(str(value))

    return " - ".join(fragments) or None


def parse_ubs_csv(file_bytes: bytes) -> list[dict[str, Any]]:
    content = file_bytes.decode("utf-8-sig")
    lines = content.splitlines()

    start = None
    for idx, line in enumerate(lines):
        if line.startswith("Date de transaction"):
            start = idx
            break

    if start is None:
        return []

    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])), delimiter=";")
    rows: list[dict[str, Any]] = []

    for raw in reader:
        label = _build_ubs_label(raw)
        amount: Decimal | None = None
        if raw.get("Débit"):
            amount = _parse_amount(raw.get("Débit"), "debit")
        elif raw.get("Crédit"):
            amount = _parse_amount(raw.get("Crédit"), "credit")

        rows.append(
            {
                "date": _parse_date(raw.get("Date de transaction") or raw.get("Date de comptabilisation")),
                "libelle": label,
                "montant": amount,
                "devise": raw.get("Monnaie") or "CHF",
                "categorie": None,
                "payee": None,
                "meta": dict(raw),
            }
        )

    return rows
