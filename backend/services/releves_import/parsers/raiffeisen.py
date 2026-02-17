"""Raiffeisen parser adapted from legacy extraction_raiffeisen.py."""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable


def _parse_date(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip().replace("T", " ")
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return text


def _parse_number(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("\u00a0", "").replace("'", "").replace("’", "").replace(",", ".")
    try:
        return Decimal(text)
    except Exception:
        return None


def _decode_content(file_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return file_bytes.decode("utf-8-sig", errors="replace")


def _extract_date(get_value: Callable[..., Any]) -> str | None:
    for columns in (("Booked at", "Booked At", "Booking Date"), ("Datum", "Date")):
        parsed = _parse_date(get_value(*columns))
        if parsed:
            return parsed
    return _parse_date(get_value("Valuta", "Valuta Date", "Value date"))


def parse_raiffeisen_csv(file_bytes: bytes) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(_decode_content(file_bytes)), delimiter=";", quotechar='"', skipinitialspace=True)
    items: list[dict[str, Any]] = []

    for line in reader:
        if not any(line.values()):
            continue
        raw = dict(line)
        normalized = {(key or "").strip().casefold(): value for key, value in line.items() if key}

        def get_value(*names: str) -> Any:
            for name in names:
                key = name.strip().casefold()
                if key in normalized:
                    return normalized[key]
            return None

        text = (get_value("Text", "Description") or "").strip()
        debit = _parse_number(get_value("Belastung CHF", "Debit CHF", "Débit CHF", "Debit"))
        credit = _parse_number(get_value("Gutschrift CHF", "Credit CHF", "Crédit CHF", "Credit"))

        amount = -abs(debit) if debit is not None else abs(credit) if credit is not None else _parse_number(get_value("Credit/Debit Amount", "Amount"))

        payee = re.sub(r"\s+", " ", text).strip() or None
        items.append(
            {
                "date": _extract_date(get_value),
                "libelle": text or None,
                "description": text or None,
                "payee": payee,
                "montant": amount,
                "devise": "CHF",
                "categorie": None,
                "meta": raw,
            }
        )

    return items
